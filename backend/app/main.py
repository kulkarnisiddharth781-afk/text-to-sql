import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, workflow
from .config import settings
from .seed import seed


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not Path(settings.database_path).exists():
        seed()
    yield


app = FastAPI(title="Text-to-SQL (read-only)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class Turn(BaseModel):
    question: str = Field(max_length=2000)
    sql: str = Field(default="", max_length=4000)
    reply: str = Field(default="", max_length=1000)  # Assistant text for turns that produced no SQL.


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=50)
    dialect: Literal["sqlite", "postgres", "mysql"] = "sqlite"


class QueryResponse(BaseModel):
    status: str
    message: str = ""
    sql: str = ""
    dialect: str = "sqlite"
    executed_sql: str = ""  # The SQLite version that ran, for non-SQLite dialects.
    explanation: str = ""
    problem: str = ""
    why_incorrect: str = ""
    optimizations: str = ""
    index_recommendations: str = ""
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    attempts: int = 0


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/schema")
def schema():
    return db.get_schema()


def _response(s: dict) -> QueryResponse:
    return QueryResponse(
        status=s.get("status", "error"),
        message=s.get("message", ""),
        sql=s.get("sql", ""),
        dialect=s.get("dialect", "sqlite"),
        executed_sql=s.get("exec_sql", "") if s.get("dialect", "sqlite") != "sqlite" else "",
        explanation=s.get("explanation", ""),
        problem=s.get("problem", ""),
        why_incorrect=s.get("why_incorrect", ""),
        optimizations=s.get("optimizations", ""),
        index_recommendations=s.get("index_recommendations", ""),
        columns=s.get("columns", []),
        rows=s.get("rows", []),
        truncated=s.get("truncated", False),
        attempts=s.get("attempts", 0),
    )


@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    try:
        s = workflow.answer(req.question.strip(), [t.model_dump() for t in req.history], req.dialect)
    except Exception as e:  # LLM/provider failures (bad key, network, model not found)
        raise HTTPException(status_code=502, detail=f"LLM request failed: {type(e).__name__}") from e
    return _response(s)


@app.post("/api/query/stream")
def query_stream(req: QueryRequest):
    def events():
        try:
            history = [t.model_dump() for t in req.history]
            for ev in workflow.stream_answer(req.question.strip(), history, req.dialect):
                if ev["type"] == "result":
                    ev = {"type": "result", **_response(ev["state"]).model_dump()}
                yield json.dumps(ev, default=str) + "\n"
        except Exception as e:  # LLM/provider failures (bad key, network, model not found)
            yield json.dumps({"type": "failure", "message": f"LLM request failed: {type(e).__name__}"}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():  # Registered last so /api routes take precedence.
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
