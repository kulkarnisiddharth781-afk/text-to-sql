"""Layer 2 defence: parse the SQL and allow only a single read-only query."""
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_ALLOWED_ROOTS = tuple(
    getattr(exp, n)
    for n in ("Select", "Union", "Except", "Intersect", "SetOperation")
    if hasattr(exp, n)
)

_FORBIDDEN = tuple(
    getattr(exp, n)
    for n in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable",
        "Command", "Merge", "TruncateTable", "Pragma", "Attach", "Detach",
        "Into", "Set", "Transaction", "Commit", "Rollback", "Use", "Copy",
    )
    if hasattr(exp, n)
)


class GuardError(Exception):
    def __init__(self, message: str, violation: bool):
        super().__init__(message)
        # True: a write/unsafe statement. False: just unparseable (worth retrying).
        self.violation = violation


def validate_read_only(sql: str, dialect: str = "sqlite") -> str:
    """Return normalised SQL in `dialect`, or raise GuardError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as e:
        raise GuardError(f"Could not parse SQL: {e}", violation=False) from e

    if len(statements) != 1:
        raise GuardError("Exactly one statement is allowed.", violation=True)

    tree = statements[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        raise GuardError("Only SELECT queries are allowed.", violation=True)
    if tree.find(*_FORBIDDEN):
        raise GuardError("Query contains a write or unsafe operation.", violation=True)

    return tree.sql(dialect=dialect, pretty=True, comments=False)
