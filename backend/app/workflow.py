"""LangGraph workflow.

classify -> retrieve_schema -> generate -> validate -> optimize -> execute -> explain

classify rejects out-of-scope, write and prompt-injection requests, or asks for clarification.
generate may also ask for clarification. validate and execute feed errors back to generate for a
bounded number of attempts.
"""
from functools import lru_cache
from typing import Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import db, prompts
from .config import settings
from .dialects import TranslationError, check_dialect, to_sqlite
from .guard import GuardError, validate_read_only
from .safety import looks_like_injection, sanitize, wrap_question
from .schema_check import SchemaError, check_schema

# All schema fields are required (no Optional) so strict structured output works on every provider.


class Intent(BaseModel):
    intent: Literal[
        "data_question", "user_sql", "write_request", "out_of_scope", "prompt_injection", "needs_clarification"
    ] = Field(description="The single best intent for the latest question.")
    message: str = Field(
        description="Polite refusal for write_request, one clarifying question for needs_clarification, otherwise an empty string."
    )


class SQLDraft(BaseModel):
    needs_clarification: bool = Field(
        description="True only if the request is ambiguous with no reasonable default, or refers to data the schema does not have."
    )
    clarifying_question: str = Field(description="One short question when needs_clarification is true, otherwise an empty string.")
    sql: str = Field(description="A single SQLite SELECT statement. Empty string when asking for clarification.")
    problem: str = Field(
        description="Only when the user supplied their own SQL and it has errors: each issue, quoting the offending part, one per line. Otherwise an empty string."
    )
    why_incorrect: str = Field(
        description="Required whenever problem is non-empty: why each issue is wrong. Otherwise an empty string."
    )


class Optimization(BaseModel):
    sql: str = Field(description="The optimised single SELECT statement, equivalent to the input.")
    optimizations: str = Field(description="Changes applied, one per line with a short reason. Empty if none.")
    index_recommendations: str = Field(
        description="CREATE INDEX statements not already in the schema, one per line followed by ' -- reason'. Empty if none help."
    )


class State(TypedDict, total=False):
    question: str
    history: list[dict]
    intent: str
    dialect: str
    schema: str
    schema_info: list[dict]
    sql: str
    exec_sql: str
    explanation: str
    problem: str
    why_incorrect: str
    optimizations: str
    index_recommendations: str
    error: str
    error_stage: str
    attempts: int
    status: Literal["ok", "refused", "clarify", "error"]
    message: str
    columns: list[str]
    rows: list[list]
    truncated: bool


@lru_cache(maxsize=1)
def _model():
    extra = {}
    if settings.llm_reasoning_effort and settings.llm_provider == "openai":
        extra["reasoning_effort"] = settings.llm_reasoning_effort
    return init_chat_model(
        settings.llm_model,
        model_provider=settings.llm_provider,
        temperature=settings.llm_temperature,
        **extra,
    )


@lru_cache(maxsize=None)
def _structured(schema: type[BaseModel]):
    return _model().with_structured_output(schema)


def _call_structured(schema: type[BaseModel], messages):
    for attempt in range(3):
        try:
            return _structured(schema).invoke(messages)
        except Exception as e:
            # Providers occasionally drop a field from the JSON; a fresh sample usually fixes it.
            if "json_validate_failed" not in str(e) or attempt == 2:
                raise
    raise AssertionError("unreachable")


def _call_text(messages) -> str:
    return str(_model().invoke(messages).content).strip()


def _clean_history(history: list[dict], dialect: str = "sqlite") -> list[dict]:
    """History comes from the client, so treat it as untrusted: sanitise it and re-validate its SQL."""
    clean = []
    for turn in history:
        sql = (turn.get("sql") or "").strip()
        reply = sanitize(turn.get("reply") or "", 600)
        if sql:
            try:
                sql = validate_read_only(sql, dialect)
            except GuardError:
                continue
        elif not reply:
            continue
        clean.append({"question": sanitize(turn.get("question") or ""), "sql": sql, "reply": reply})
    return clean[-settings.max_history_turns :]


def _history_messages(history: list[dict]) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for turn in history:
        messages += [("human", wrap_question(turn["question"])), ("ai", turn["sql"] or turn["reply"])]
    return messages


def classify(state: State) -> State:
    """Intent detection and scope validation."""
    question = state["question"]
    if looks_like_injection(question):
        return {"intent": "prompt_injection", "status": "refused", "message": prompts.INJECTION_REFUSAL}
    tables = ", ".join(t["name"] for t in db.get_schema())
    draft = _call_structured(
        Intent,
        [
            ("system", prompts.classify_system(tables)),
            *_history_messages(state["history"]),
            ("human", wrap_question(question)),
        ],
    )
    out = {"intent": draft.intent}
    if draft.intent == "out_of_scope":
        return {**out, "status": "refused", "message": prompts.OUT_OF_SCOPE}
    if draft.intent == "prompt_injection":
        return {**out, "status": "refused", "message": prompts.INJECTION_REFUSAL}
    if draft.intent == "write_request":
        return {**out, "status": "refused", "message": draft.message or prompts.REFUSAL}
    if draft.intent == "needs_clarification":
        return {**out, "status": "clarify", "message": draft.message or prompts.CLARIFY_FALLBACK}
    return out


def retrieve_schema(state: State) -> State:
    tables = db.get_schema()
    return {"schema_info": tables, "schema": db.schema_as_text(tables)}


def generate(state: State) -> State:
    attempts = state.get("attempts", 0) + 1
    messages = [
        (
            "system",
            prompts.generate_system(
                state["schema"], settings.max_rows, state.get("intent") == "user_sql", state["dialect"]
            ),
        ),
        *_history_messages(state["history"]),
        ("human", wrap_question(state["question"])),
    ]
    if state.get("error"):
        messages += [
            ("ai", state.get("sql", "")),
            ("human", f"That query failed with: {state['error']}\nReturn a corrected read-only query."),
        ]
    draft = _call_structured(SQLDraft, messages)
    if state.get("intent") == "user_sql":
        # The model occasionally asks a question about pasted SQL; push it to return the query instead.
        for _ in range(2):
            if draft.sql.strip() and not draft.needs_clarification:
                break
            nudge = "Do not ask questions. Return the user's SQL, corrected if needed, as one SELECT in `sql`."
            draft = _call_structured(SQLDraft, [*messages, ("human", nudge)])
    if draft.needs_clarification or not draft.sql.strip():
        return {
            "attempts": attempts,
            "status": "clarify",
            "message": draft.clarifying_question or prompts.CLARIFY_FALLBACK,
        }
    return {
        "attempts": attempts,
        "sql": draft.sql,
        # A retry draft may omit the diagnosis, so keep the first one.
        "problem": draft.problem or state.get("problem", ""),
        "why_incorrect": draft.why_incorrect or state.get("why_incorrect", ""),
        "error": "",
    }


def _validate_sql(sql: str, schema_info: list[dict], dialect: str) -> tuple[str, str]:
    """Read-only guard in the user's dialect, translate to SQLite, re-check, then schema check.

    Returns (sql in dialect, sql to execute). Raises GuardError, TranslationError or SchemaError.
    """
    check_dialect(sql, dialect)
    safe = validate_read_only(sql, dialect)
    exec_sql = validate_read_only(to_sqlite(safe, dialect))
    check_schema(exec_sql, schema_info)
    return safe, exec_sql


def validate(state: State) -> State:
    try:
        sql, exec_sql = _validate_sql(state["sql"], state["schema_info"], state["dialect"])
        return {"sql": sql, "exec_sql": exec_sql, "error": "", "error_stage": ""}
    except GuardError as e:
        if e.violation:
            return {"status": "refused", "message": prompts.REFUSAL, "sql": ""}
        return {"error": str(e), "error_stage": "validate"}
    except (SchemaError, TranslationError) as e:
        return {"error": str(e), "error_stage": "validate"}


def optimize(state: State) -> State:
    """Rewrite SQL the user supplied; the rewrite is validated and discarded if it fails."""
    if state.get("intent") != "user_sql":
        return {"error": ""}
    draft = _call_structured(
        Optimization,
        [
            ("system", prompts.optimize_system(state["schema"], state["dialect"])),
            ("human", f"{wrap_question(state['question'])}\n<current_sql>\n{state['sql']}\n</current_sql>"),
        ],
    )
    out: State = {"error": "", "index_recommendations": draft.index_recommendations}
    if not draft.sql.strip():
        return {**out, "optimizations": draft.optimizations}
    try:
        out["sql"], out["exec_sql"] = _validate_sql(draft.sql, state["schema_info"], state["dialect"])
        out["optimizations"] = draft.optimizations
    except (GuardError, SchemaError, TranslationError) as e:
        out["optimizations"] = f"The suggested rewrite was discarded because it failed validation: {e}"
    return out


def execute(state: State) -> State:
    try:
        columns, rows, truncated = db.run_query(state["exec_sql"])
    except db.QueryError as e:
        return {"error": str(e), "error_stage": "execute"}
    return {"status": "ok", "columns": columns, "rows": rows, "truncated": truncated, "error": ""}


def explain(state: State) -> State:
    try:
        text = _call_text(
            [
                ("system", prompts.EXPLAIN_SYSTEM),
                ("human", f"{wrap_question(state['question'])}\n<sql>\n{state['sql']}\n</sql>"),
            ]
        )
    except Exception:
        text = ""  # The results are already valid; do not lose them because the explanation failed.
    return {"explanation": text.replace("\u202f", " ").replace("\u00a0", " ")}


def fail(state: State) -> State:
    if state.get("dialect") != "sqlite" and state.get("error_stage") == "execute":
        # The SQL is valid in its dialect; only the SQLite demo database could not run it.
        return {
            "status": "ok",
            "columns": [],
            "rows": [],
            "truncated": False,
            "message": (
                "This query is valid, but the SQLite demo database could not run it, so there is no "
                f"result preview. Reason: {state.get('error', '')}"
            ),
        }
    return {
        "status": "error",
        "message": (
            "I couldn't build a valid query for that. Try rephrasing or naming the tables and columns "
            f"you mean. Last error: {state.get('error', '')}"
        ),
    }


def _after_classify(state: State) -> str:
    return END if state.get("status") in ("refused", "clarify") else "retrieve_schema"


def _after_generate(state: State) -> str:
    return END if state.get("status") == "clarify" else "validate"


def _after_check(next_node: str):
    def route(state: State) -> str:
        if state.get("status") == "refused":
            return END
        if not state.get("error"):
            return next_node
        return "generate" if state.get("attempts", 0) <= settings.max_retries else "fail"

    return route


def _build():
    g = StateGraph(State)
    for name, fn in [
        ("classify", classify),
        ("retrieve_schema", retrieve_schema),
        ("generate", generate),
        ("validate", validate),
        ("optimize", optimize),
        ("execute", execute),
        ("explain", explain),
        ("fail", fail),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", _after_classify, ["retrieve_schema", END])
    g.add_edge("retrieve_schema", "generate")
    g.add_conditional_edges("generate", _after_generate, ["validate", END])
    g.add_conditional_edges("validate", _after_check("optimize"), ["optimize", "generate", "fail", END])
    g.add_edge("optimize", "execute")
    g.add_conditional_edges("execute", _after_check("explain"), ["explain", "generate", "fail", END])
    g.add_edge("explain", END)
    g.add_edge("fail", END)
    return g.compile()


graph = _build()


def _initial(question: str, history: list[dict] | None, dialect: str) -> State:
    return {
        "question": question,
        "history": _clean_history(history or [], dialect),
        "dialect": dialect,
        "attempts": 0,
    }


def answer(question: str, history: list[dict] | None = None, dialect: str = "sqlite") -> State:
    return graph.invoke(_initial(question, history, dialect))


def _sql_event(state: State) -> dict:
    return {
        "type": "sql",
        "sql": state["sql"],
        "problem": state.get("problem", ""),
        "why_incorrect": state.get("why_incorrect", ""),
        "optimizations": state.get("optimizations", ""),
        "index_recommendations": state.get("index_recommendations", ""),
    }


def stream_answer(question: str, history: list[dict] | None = None, dialect: str = "sqlite"):
    """Yield progress events, ending with one {"type": "result", "state": ...} event."""
    yield {"type": "status", "message": "Understanding your request…"}
    state: State = {"dialect": dialect}  # Node updates only carry changed keys, so seed what the response needs.
    for update in graph.stream(_initial(question, history, dialect), stream_mode="updates"):
        for node, delta in update.items():
            state.update(delta)
            if state.get("status") in ("refused", "clarify"):
                continue
            retrying = bool(delta.get("error")) and state.get("attempts", 0) <= settings.max_retries
            if node == "classify":
                yield {"type": "status", "message": "Reading the schema…"}
            elif node == "retrieve_schema":
                yield {"type": "status", "message": "Generating SQL…"}
            elif node == "generate" and state.get("sql"):
                yield _sql_event(state)
                yield {"type": "status", "message": "Validating against the schema…"}
            elif node == "validate":
                if retrying:
                    yield {"type": "status", "message": "Query did not validate, fixing it…"}
                elif not delta.get("error"):
                    nxt = "Optimizing your query…" if state.get("intent") == "user_sql" else "Running query…"
                    yield {"type": "status", "message": nxt}
            elif node == "optimize" and state.get("intent") == "user_sql":
                yield _sql_event(state)
                yield {"type": "status", "message": "Running query…"}
            elif node == "execute":
                if retrying:
                    yield {"type": "status", "message": "Query failed, fixing it…"}
                elif not delta.get("error"):
                    yield {"type": "status", "message": "Writing explanation…"}
    yield {"type": "result", "state": state}
