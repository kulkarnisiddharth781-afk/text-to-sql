import pytest

from app import db
from app.config import settings
from app.guard import GuardError, validate_read_only
from app.seed import seed


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers",
        "WITH t AS (SELECT country, COUNT(*) c FROM customers GROUP BY country) SELECT * FROM t",
        "SELECT 1 UNION SELECT 2",
    ],
)
def test_allows_reads(sql):
    assert validate_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE customers SET name='x'",
        "INSERT INTO customers VALUES (1,'a','b','c')",
        "DROP TABLE customers",
        "CREATE TABLE x (a int)",
        "SELECT 1; DROP TABLE customers",
        "PRAGMA writable_schema=1",
        "ATTACH DATABASE 'x.db' AS x",
    ],
)
def test_blocks_writes(sql):
    with pytest.raises(GuardError):
        validate_read_only(sql)


def test_db_connection_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    seed(str(path))
    monkeypatch.setattr(settings, "database_path", str(path))
    with pytest.raises(db.QueryError):
        db.run_query("DELETE FROM customers")
    cols, rows, _ = db.run_query("SELECT COUNT(*) FROM customers")
    assert rows[0][0] == 100
