"""SQL validation against the real schema: tables, columns and joins must exist and make sense."""
import difflib

from sqlglot import exp, parse_one
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope


class SchemaError(Exception):
    """The SQL refers to something that is not in the schema, or joins tables incorrectly."""


def _affinity(declared: str | None) -> str:
    t = (declared or "").upper()
    if "INT" in t or any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "numeric"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "text"
    return "none"


def _columns_hint(tables: list[str], columns: dict[str, dict[str, str]]) -> str:
    return "; ".join(f"{t}({', '.join(columns[t])})" for t in tables if t in columns)


def _check_tables(tree: exp.Expression, names: dict[str, str]) -> list[str]:
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # table-valued function
        name = table.name.lower()
        if name in ctes:
            continue
        if name not in names:
            close = difflib.get_close_matches(name, names, n=1)
            hint = f" Did you mean '{names[close[0]]}'?" if close else ""
            raise SchemaError(
                f"Table '{table.name}' does not exist.{hint} Available tables: {', '.join(names.values())}."
            )
        if name not in used:
            used.append(name)
    return used


def _check_joins(
    qualified: exp.Expression,
    columns: dict[str, dict[str, str]],
    fks: list[tuple[str, str, str, str]],
) -> None:
    for scope in traverse_scope(qualified):
        sources = {a.lower(): s.name.lower() for a, s in scope.sources.items() if isinstance(s, exp.Table)}
        conditions = [j.args["on"] for j in scope.expression.args.get("joins") or [] if j.args.get("on")]
        if scope.expression.args.get("where"):
            conditions.append(scope.expression.args["where"])
        for cond in conditions:
            for eq in cond.find_all(exp.EQ):
                left, right = eq.this, eq.expression
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                ta, tb = sources.get(left.table.lower()), sources.get(right.table.lower())
                if not ta or not tb or left.table == right.table:
                    continue
                ca, cb = left.name.lower(), right.name.lower()
                kinds = {_affinity(columns[ta].get(ca)), _affinity(columns[tb].get(cb))}
                if kinds == {"numeric", "text"}:
                    raise SchemaError(
                        f"Invalid join: {ta}.{ca} ({columns[ta].get(ca)}) is compared with "
                        f"{tb}.{cb} ({columns[tb].get(cb)}); the types do not match."
                    )
                related = [f for f in fks if {f[0], f[2]} == {ta, tb}]
                allowed = {(f[0], f[1], f[2], f[3]) for f in related} | {(f[2], f[3], f[0], f[1]) for f in related}
                if related and (ta, ca, tb, cb) not in allowed:
                    declared = ", ".join(f"{f[0]}.{f[1]} = {f[2]}.{f[3]}" for f in related)
                    raise SchemaError(
                        f"Invalid join: {ta}.{ca} = {tb}.{cb} does not follow a declared relationship. "
                        f"Join {ta} and {tb} on: {declared}."
                    )


def check_schema(sql: str, tables: list[dict]) -> None:
    """Raise SchemaError unless every table, column and join in `sql` exists in `tables`."""
    columns = {t["name"].lower(): {c["name"].lower(): c["type"] for c in t["columns"]} for t in tables}
    names = {t["name"].lower(): t["name"] for t in tables}
    fks = [
        (t["name"].lower(), f["column"].lower(), f["ref_table"].lower(), f["ref_column"].lower())
        for t in tables
        for f in t["foreign_keys"]
    ]
    try:
        tree = parse_one(sql, read="sqlite")
    except SqlglotError as e:
        raise SchemaError(f"Could not parse SQL: {e}") from e

    used = _check_tables(tree, names)
    try:
        qualified = qualify(tree.copy(), schema=columns, dialect="sqlite", validate_qualify_columns=True, identify=False)
    except OptimizeError as e:
        raise SchemaError(f"{str(e).split('. Line')[0]}. Columns available: {_columns_hint(used, columns)}.") from e
    except SqlglotError:
        return  # Unusual syntax sqlglot cannot analyse; SQLite still rejects real errors at execution.
    _check_joins(qualified, columns, fks)
