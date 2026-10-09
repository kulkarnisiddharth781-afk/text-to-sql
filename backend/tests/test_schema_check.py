import pytest

from app import db
from app.config import settings
from app.schema_check import SchemaError, check_schema
from app.seed import seed


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "t.db"
    seed(str(path))
    original = settings.database_path
    settings.database_path = str(path)
    try:
        yield db.get_schema()
    finally:
        settings.database_path = original


VALID = [
    "SELECT * FROM customers",
    "SELECT c.name, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
    "WITH t AS (SELECT country, COUNT(*) AS n FROM customers GROUP BY country) SELECT country, n FROM t ORDER BY n DESC",
    "SELECT strftime('%Y-%m', order_date) AS m, COUNT(*) FROM orders GROUP BY m",
    "SELECT c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
    "SELECT p.category, SUM(oi.quantity * p.price) AS rev FROM order_items oi "
    "JOIN products p ON p.id = oi.product_id JOIN orders o ON o.id = oi.order_id "
    "WHERE o.status = 'completed' GROUP BY p.category ORDER BY rev DESC",
    "SELECT name FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status = 'shipped')",
    "SELECT c.name FROM customers c, orders o WHERE c.id = o.customer_id AND o.status = 'completed'",
    "SELECT a.name, b.name FROM customers a JOIN customers b ON a.country = b.country AND a.id < b.id",
    "SELECT name, ROW_NUMBER() OVER (PARTITION BY country ORDER BY signup_date) AS rn FROM customers",
]


@pytest.mark.parametrize("sql", VALID)
def test_valid_queries_pass(sql, tables):
    check_schema(sql, tables)


@pytest.mark.parametrize(
    "sql, fragment",
    [
        ("SELECT nam FROM customers", "could not be resolved"),
        ("SELECT c.state FROM customers c", "state"),
        ("SELECT * FROM users", "does not exist"),
        ("SELECT * FROM sqlite_master", "does not exist"),
        ("SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id", "could not be resolved"),
        ("SELECT c.name FROM customers c JOIN orders o ON o.id = c.id", "declared relationship"),
        ("SELECT c.name FROM customers c, orders o WHERE o.id = c.id", "declared relationship"),
        ("SELECT c.name FROM customers c JOIN orders o ON o.order_date = c.id", "types do not match"),
    ],
)
def test_invalid_queries_are_rejected(sql, fragment, tables):
    with pytest.raises(SchemaError, match=fragment):
        check_schema(sql, tables)


def test_unknown_table_suggests_closest_match(tables):
    with pytest.raises(SchemaError, match="Did you mean 'customers'"):
        check_schema("SELECT * FROM customer", tables)


def test_error_lists_available_columns(tables):
    with pytest.raises(SchemaError, match=r"customers\(id, name, country, signup_date\)"):
        check_schema("SELECT nam FROM customers", tables)
