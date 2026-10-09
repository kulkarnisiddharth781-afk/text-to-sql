"""Multi-dialect support: SQL is written and validated in the chosen dialect, then translated to run on SQLite."""
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

DIALECTS = {"sqlite": "SQLite", "postgres": "PostgreSQL", "mysql": "MySQL"}

HINTS = {
    "postgres": "CURRENT_DATE - INTERVAL '90 days', EXTRACT(YEAR FROM col), TO_CHAR(col, 'YYYY-MM'), ILIKE, || to concatenate",
    "mysql": "CURDATE() - INTERVAL 90 DAY, YEAR(col), DATE_FORMAT(col, '%Y-%m'), CONCAT(...)",
}
_SQLITE_ONLY = re.compile(r"\b(?:strftime|julianday|datetime)\s*\(|\bdate\s*\(\s*'now'", re.I)

_STRFTIME = {"YEAR": "%Y", "MONTH": "%m", "DAY": "%d", "HOUR": "%H", "MINUTE": "%M", "SECOND": "%S", "DOY": "%j", "WEEK": "%W"}
_TRUNC_MODIFIER = {"YEAR": "start of year", "MONTH": "start of month", "DAY": None}


class TranslationError(Exception):
    """The query is valid in its dialect but cannot be translated to SQLite."""


def _fn(name: str, *args: exp.Expression) -> exp.Expression:
    return exp.Anonymous(this=name, expressions=list(args))


def _date_part(fmt: str, arg: exp.Expression) -> exp.Expression:
    return exp.Cast(this=_fn("STRFTIME", exp.Literal.string(fmt), arg), to=exp.DataType.build("int"))


def _shift(base: exp.Expression, amount: str, unit: str, sign: int) -> exp.Expression:
    unit = unit.lower().rstrip("s") + "s"
    number = amount.strip().split()[0] if amount.strip() else "0"
    return _fn("DATE", base, exp.Literal.string(f"{'-' if sign < 0 else '+'}{number.lstrip('+-')} {unit}"))


def _interval_parts(interval: exp.Interval) -> tuple[str, str] | None:
    value = interval.this.name if interval.this is not None else ""
    unit = interval.args.get("unit")
    if unit is not None:
        return value, unit.name
    words = value.split()  # PostgreSQL style: INTERVAL '30 days'
    return (words[0], words[1]) if len(words) == 2 else None


def _rewrite(node: exp.Expression) -> exp.Expression:
    """Replace date functions that SQLite lacks with STRFTIME and DATE equivalents."""
    if isinstance(node, exp.Extract):
        fmt = _STRFTIME.get(node.this.name.upper())
        if fmt:
            return _date_part(fmt, node.expression)
    elif isinstance(node, (exp.Year, exp.Month, exp.Day)):
        return _date_part(_STRFTIME[type(node).__name__.upper()], node.this)
    elif isinstance(node, (exp.DateTrunc, exp.TimestampTrunc)):
        unit = node.args.get("unit")
        key = unit.name.upper() if unit is not None else ""
        if key in _TRUNC_MODIFIER:
            modifier = _TRUNC_MODIFIER[key]
            args = [node.this] + ([exp.Literal.string(modifier)] if modifier else [])
            return _fn("DATE", *args)
    elif isinstance(node, (exp.Add, exp.Sub)) and isinstance(node.expression, exp.Interval):
        parts = _interval_parts(node.expression)
        if parts:
            return _shift(node.this, parts[0], parts[1], 1 if isinstance(node, exp.Add) else -1)
    elif isinstance(node, (exp.DateAdd, exp.DateSub)):
        unit = node.args.get("unit")
        if unit is not None:
            return _shift(node.this, node.expression.name, unit.name, 1 if isinstance(node, exp.DateAdd) else -1)
    return node


def check_dialect(sql: str, dialect: str) -> None:
    """Reject SQLite-only date functions in other dialects; the parser can silently drop their arguments."""
    if dialect != "sqlite" and _SQLITE_ONLY.search(sql):
        raise TranslationError(
            f"The query uses SQLite-only date functions (strftime, julianday, date('now', ...)). "
            f"Write it in {DIALECTS[dialect]}, for example: {HINTS[dialect]}."
        )


def to_sqlite(sql: str, dialect: str) -> str:
    """Translate validated read-only SQL from `dialect` to SQLite so the sample database can run it."""
    if dialect == "sqlite":
        return sql
    try:
        tree = sqlglot.parse_one(sql, read=dialect).transform(_rewrite)
        return tree.sql(dialect="sqlite", unsupported_level=ErrorLevel.RAISE, comments=False)
    except SqlglotError as e:
        raise TranslationError(f"Could not translate this {DIALECTS[dialect]} query to SQLite: {e}") from e
