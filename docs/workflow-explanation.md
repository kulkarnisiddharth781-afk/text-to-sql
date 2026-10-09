# LangGraph and LangChain workflow explanation

## 1. How the two frameworks are used

| Framework | Role in this project |
|---|---|
| **LangGraph** | Runs the agent as a state machine: named steps (nodes), shared state, and conditional routing, including bounded retry loops. It is defined in `backend/app/workflow.py`. |
| **LangChain** | Gives one interface to the language model. `init_chat_model` picks the provider (Groq, OpenAI, Anthropic or Ollama) from environment variables. `with_structured_output` makes the model return validated JSON that matches a Pydantic class. |

The workflow is a LangGraph `StateGraph` over a `State` dictionary. Each node reads that state and returns only the keys it changes. Conditional edges read the state to decide where to go next.

## 2. Workflow at a glance

```text
classify > retrieve_schema > generate > validate > optimize > execute > explain
```

Four nodes call the model (`classify`, `generate`, `optimize`, `explain`). Three are plain code (`retrieve_schema`, `validate`, `execute`). Splitting the work this way means the model proposes and ordinary code decides what is allowed to run.

## 3. The shared state

Every node reads and writes one `State` object. The main keys:

| Group | Keys |
|---|---|
| Input | `question`, `history` (cleaned earlier turns), `dialect` |
| Understanding | `intent`, `schema` (text for the prompt), `schema_info` (structured, used for validation) |
| SQL | `sql` (in the chosen dialect), `exec_sql` (the SQLite version that runs) |
| Diagnosis | `problem`, `why_incorrect`, `optimizations`, `index_recommendations` |
| Control | `attempts`, `error`, `error_stage`, `status` (`ok`, `refused`, `clarify`, `error`), `message` |
| Output | `columns`, `rows`, `truncated`, `explanation` |

## 4. Node by node

**1. `classify` (model call, structured output `Intent`)**
Intent detection and scope check in one step.

- Before any model call, a pattern screen checks for prompt injection and short-circuits obvious attacks with no model call at all.
- Otherwise the model returns one of six intents: `data_question`, `user_sql`, `write_request`, `out_of_scope`, `prompt_injection`, `needs_clarification`.
- Out-of-scope, write and injection requests end the run here, before any SQL exists. Out-of-scope uses a fixed message.
- A vague request ends the run with a clarifying question.

**2. `retrieve_schema` (no model)**
Reads tables, columns, types, foreign keys and indexes from the live database. It produces the schema text for the prompts and a structured copy for validation, from the same read.

**3. `generate` (model call, structured output `SQLDraft`)**
Writes one SELECT in the chosen dialect.

- **Follow-ups:** earlier turns are given to the model as conversation messages, and the prompt tells it to modify the most recent SQL instead of starting over.
- **Debugging:** if the user pasted SQL, it returns the issue, the reason it is wrong, and a corrected query.
- **Clarification:** if the request is ambiguous or needs data the schema lacks (for example a "state" column), it asks one question instead of guessing.
- **Retry:** on a retry, the previous SQL and the exact error are added to the conversation, so the model fixes the specific problem.

**4. `validate` (no model)**
A chain of code checks, in order:

1. SQLite-only function check for PostgreSQL and MySQL
2. Read-only parse in the chosen dialect (single SELECT only, no writes, comments stripped)
3. Translation to SQLite and a second read-only check
4. Schema check: every table and column exists, joins follow a declared foreign key and have matching types

A destructive statement ends the run with a refusal. Any other failure sets `error` and loops back to `generate`.

**5. `optimize` (model call, structured output `Optimization`)**
Runs only when the user pasted SQL. It rewrites the query for readability and performance, removes unnecessary joins, and recommends indexes. The rewrite goes through the same validation, and if it fails the original is kept and a note explains why.

**6. `execute` (no model)**
Runs the SQL on a read-only database connection with a 200-row cap and a 5-second timeout. A runtime error sets `error` and loops back to `generate`.

**7. `explain` (model call, plain text)**
Writes a one-to-two-sentence plain-English explanation of the final, validated SQL. If it fails, the results are still returned.

## 5. Routing (conditional edges)

| After | Condition | Goes to |
|---|---|---|
| `classify` | refused or clarify | End |
| `classify` | otherwise | `retrieve_schema` |
| `generate` | clarify | End |
| `generate` | otherwise | `validate` |
| `validate` | destructive SQL | End (refused) |
| `validate` | error and attempts left | `generate` (retry) |
| `validate` | error and no attempts left | `fail` |
| `validate` | no error | `optimize` |
| `execute` | error and attempts left | `generate` (retry) |
| `execute` | error and no attempts left | `fail` |
| `execute` | no error | `explain` |
| `explain`, `fail` | always | End |

**Bounded loop:** the retry counter `attempts` is checked against `MAX_RETRIES` (default 2), so the agent can never loop forever. If it runs out of attempts, `fail` returns a friendly error.

## 6. LangChain usage

- **Model creation:** `init_chat_model(model, model_provider, temperature)` builds the chat model from environment settings. The same code runs on Groq, OpenAI, Anthropic or Ollama.
- **Structured output:** `with_structured_output(SomePydanticClass)` is used for three schemas. Every field is required, so output works across providers.
  - `Intent`: `intent`, `message`
  - `SQLDraft`: `needs_clarification`, `clarifying_question`, `sql`, `problem`, `why_incorrect`
  - `Optimization`: `sql`, `optimizations`, `index_recommendations`
- **Messages:** prompts are built as system, human and AI messages. Earlier turns become human and AI messages, which is how context is kept.
- **Reliability:** if a provider drops a field from its JSON, the call is retried up to three times.
- **Prompt safety:** user text is wrapped in `<question>` tags, and the prompts state that it is data, never instructions.

The prompts themselves are listed in [prompts.md](prompts.md).

## 7. Streaming

The graph is run with LangGraph's `stream` in `updates` mode, which yields each node's changes as it finishes. The server turns these into progress events (current step, the generated SQL, then the final result), so the UI shows the SQL while validation and execution are still running.

## 8. Example trace: "Show all customers", then "Only those from Germany"

| Step | What happens |
|---|---|
| `classify` | Intent `data_question`, with the earlier turn as context |
| `retrieve_schema` | Loads the four tables and their relationships |
| `generate` | Sees the previous SQL `SELECT * FROM customers` and returns `SELECT * FROM customers WHERE country = 'Germany'` |
| `validate` | Single SELECT, table and column exist, so it passes |
| `execute` | Returns the German customers |
| `explain` | "Returns all customers whose country is Germany." |

Total: 3 model calls.

## 9. Why this design

- Scope and safety decisions happen before SQL generation, so out-of-scope and unsafe requests cost almost nothing.
- The model's output is always checked by code before it touches the database.
- Errors go back to the model as exact messages, so corrections are targeted.
- The explicit graph makes every step, branch and retry visible and testable. The 113 tests replace the model with a fake and check routing, retries and refusals offline.
