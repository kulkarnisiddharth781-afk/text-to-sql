import json

import pytest
from fastapi.testclient import TestClient

from app import db, workflow
from app.dialects import TranslationError, check_dialect, to_sqlite
from app.guard import GuardError, validate_read_only
from app.main import app
from app.workflow import Intent, SQLDraft

PORTABLE = [
    ("postgres", "SELECT EXTRACT(YEAR FROM order_date::date) AS y, COUNT(*) AS n FROM orders GROUP BY 1"),
    ("postgres", "SELECT to_char(order_date::date, 'YYYY-MM') AS m, COUNT(*) FROM orders GROUP BY 1"),
    ("postgres", "SELECT name FROM customers WHERE name ILIKE '%an%' LIMIT 5"),
    ("postgres", "SELECT date_trunc('month', order_date::date) AS m, COUNT(*) FROM orders GROUP BY 1"),
    ("postgres", "SELECT COUNT(*) FROM customers WHERE signup_date::date >= CURRENT_DATE - INTERVAL '30 days'"),
    ("postgres", "SELECT country, STRING_AGG(name, ', ') FROM customers GROUP BY country"),
    ("mysql", "SELECT YEAR(order_date) AS y, COUNT(*) FROM `orders` GROUP BY y"),
    ("mysql", "SELECT DATE_FORMAT(order_date, '%Y-%m') AS m, COUNT(*) FROM orders GROUP BY m"),
    ("mysql", "SELECT CONCAT(name, ' - ', country) FROM customers LIMIT 3"),
    ("mysql", "SELECT IFNULL(country, 'n/a') FROM customers LIMIT 2"),
    ("mysql", "SELECT COUNT(*) FROM orders WHERE order_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"),
]


@pytest.mark.parametrize("dialect, sql", PORTABLE)
def test_dialect_queries_run_on_the_sample_database(seeded_db, dialect, sql):
    safe = validate_read_only(sql, dialect)
    columns, rows, _ = db.run_query(to_sqlite(safe, dialect))
    assert columns and rows


@pytest.mark.parametrize(
    "dialect, sql",
    [
        ("postgres", "DELETE FROM customers"),
        ("postgres", "DROP TABLE customers"),
        ("postgres", "COPY customers TO '/tmp/x'"),
        ("postgres", "SELECT 1; DELETE FROM orders"),
        ("mysql", "UPDATE customers SET name = 'x'"),
        ("mysql", "TRUNCATE TABLE orders"),
        ("mysql", "SET GLOBAL read_only = 0"),
    ],
)
def test_writes_are_blocked_in_every_dialect(dialect, sql):
    with pytest.raises(GuardError):
        validate_read_only(sql, dialect)


def test_sqlite_is_passed_through_unchanged():
    assert to_sqlite("SELECT 1", "sqlite") == "SELECT 1"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers WHERE signup_date >= DATE('now', '-90 day')",
        "SELECT STRFTIME('%Y', order_date) FROM orders",
        "SELECT JULIANDAY(order_date) FROM orders",
    ],
)
@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_sqlite_only_functions_are_rejected_outside_sqlite(sql, dialect):
    with pytest.raises(TranslationError, match="SQLite-only"):
        check_dialect(sql, dialect)
    check_dialect(sql, "sqlite")


def test_workflow_feeds_sqlite_only_functions_back_for_a_rewrite(llm):
    push(llm, "SELECT * FROM customers WHERE signup_date >= DATE('now', '-90 day')")
    llm.push(
        SQLDraft,
        needs_clarification=False,
        clarifying_question="",
        sql="SELECT * FROM customers WHERE signup_date::date >= CURRENT_DATE - INTERVAL '90 days'",
        problem="",
        why_incorrect="",
    )
    r = workflow.answer("recent customers", dialect="postgres")
    assert r["status"] == "ok" and r["attempts"] == 2
    assert any("SQLite-only" in text for _, text in llm.messages[-1])


def test_untranslatable_sql_raises():
    with pytest.raises(TranslationError):
        to_sqlite(validate_read_only("SELECT * FROM customers TABLESAMPLE SYSTEM (10)", "postgres"), "postgres")


def push(llm, sql):
    llm.push(Intent, intent="data_question", message="")
    llm.push(
        SQLDraft, needs_clarification=False, clarifying_question="", sql=sql, problem="", why_incorrect=""
    )


def test_workflow_runs_postgres_sql_and_reports_the_translation(llm):
    push(llm, "SELECT EXTRACT(YEAR FROM order_date::date) AS y, COUNT(*) AS n FROM orders GROUP BY 1")
    r = workflow.answer("orders per year", dialect="postgres")
    assert r["status"] == "ok" and r["rows"]
    assert "EXTRACT" in r["sql"].upper() and "STRFTIME" in r["exec_sql"].upper()


def test_dialect_is_given_to_the_model(llm):
    push(llm, "SELECT name FROM customers LIMIT 3")
    workflow.answer("names", dialect="mysql")
    system = next(text for role, text in llm.messages[-1] if role == "system")
    assert "MySQL" in system


def test_valid_sql_the_demo_database_cannot_run_is_returned_without_results(llm):
    llm.push(Intent, intent="data_question", message="")
    for _ in range(3):
        llm.push(
            SQLDraft,
            needs_clarification=False,
            clarifying_question="",
            sql="SELECT FANCY_FUNC(name) FROM customers",
            problem="",
            why_incorrect="",
        )
    r = workflow.answer("fancy", dialect="postgres")
    assert r["status"] == "ok" and r["rows"] == [] and "could not run it" in r["message"]


def test_sqlite_runtime_failure_is_still_an_error(llm):
    llm.push(Intent, intent="data_question", message="")
    for _ in range(3):
        llm.push(
            SQLDraft,
            needs_clarification=False,
            clarifying_question="",
            sql="SELECT FANCY_FUNC(name) FROM customers",
            problem="",
            why_incorrect="",
        )
    assert workflow.answer("fancy", dialect="sqlite")["status"] == "error"


def test_api_accepts_known_dialects_only(llm):
    client = TestClient(app)
    assert client.post("/api/query", json={"question": "x", "dialect": "oracle"}).status_code == 422
    push(llm, "SELECT name FROM customers LIMIT 2")
    r = client.post("/api/query", json={"question": "names", "dialect": "postgres"})
    assert r.status_code == 200 and r.json()["dialect"] == "postgres"


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_streamed_result_reports_the_selected_dialect(llm, dialect):
    push(llm, "SELECT YEAR(order_date) AS y FROM orders" if dialect == "mysql" else "SELECT name FROM customers")
    with TestClient(app).stream("POST", "/api/query/stream", json={"question": "x", "dialect": dialect}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1]["type"] == "result" and events[-1]["dialect"] == dialect


def test_streamed_refusal_still_reports_the_dialect(llm):
    llm.push(Intent, intent="out_of_scope", message="")
    with TestClient(app).stream("POST", "/api/query/stream", json={"question": "x", "dialect": "mysql"}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1]["status"] == "refused" and events[-1]["dialect"] == "mysql"
