"""Layer 3 defence: the database connection itself is read-only."""
import sqlite3
import time
from pathlib import Path

from .config import settings


class QueryError(Exception):
    pass


def _connect() -> sqlite3.Connection:
    path = Path(settings.database_path).resolve()
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def get_schema() -> list[dict]:
    with _connect() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        result = []
        for (name,) in tables:
            cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
            indexes = []
            for idx in conn.execute(f'PRAGMA index_list("{name}")').fetchall():
                idx_cols = conn.execute(f'PRAGMA index_info("{idx[1]}")').fetchall()
                indexes.append({"name": idx[1], "unique": bool(idx[2]), "columns": [c[2] for c in idx_cols]})
            result.append(
                {
                    "name": name,
                    "columns": [{"name": c[1], "type": c[2], "pk": bool(c[5])} for c in cols],
                    "foreign_keys": [{"column": f[3], "ref_table": f[2], "ref_column": f[4]} for f in fks],
                    "indexes": indexes,
                }
            )
        return result


def schema_as_text(tables: list[dict] | None = None) -> str:
    lines = []
    for t in tables if tables is not None else get_schema():
        cols = ", ".join(f"{c['name']} {c['type']}{' PK' if c['pk'] else ''}" for c in t["columns"])
        lines.append(f"TABLE {t['name']} ({cols})")
        for fk in t["foreign_keys"]:
            lines.append(f"  FK {t['name']}.{fk['column']} -> {fk['ref_table']}.{fk['ref_column']}")
        for ix in t["indexes"]:
            kind = "UNIQUE INDEX" if ix["unique"] else "INDEX"
            lines.append(f"  {kind} {ix['name']} ({', '.join(ix['columns'])})")
    return "\n".join(lines)


def run_query(sql: str) -> tuple[list[str], list[list], bool]:
    conn = _connect()
    deadline = time.monotonic() + settings.query_timeout_s
    # A non-zero return from the progress handler aborts the running query.
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        fetched = cur.fetchmany(settings.max_rows + 1)
    except sqlite3.Error as e:
        raise QueryError(str(e)) from e
    finally:
        conn.close()
    truncated = len(fetched) > settings.max_rows
    return columns, [list(r) for r in fetched[: settings.max_rows]], truncated
