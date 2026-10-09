-- Sample database schema: a small e-commerce store (SQLite).
-- Relationships: orders.customer_id -> customers.id
--                order_items.order_id -> orders.id
--                order_items.product_id -> products.id
-- Dates are stored as ISO-8601 text (YYYY-MM-DD).
-- `python -m app.seed` creates this schema and fills it with deterministic sample data.

CREATE TABLE customers (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  country     TEXT NOT NULL,
  signup_date TEXT NOT NULL
);

CREATE TABLE products (
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL,
  category TEXT NOT NULL,
  price    REAL NOT NULL
);

CREATE TABLE orders (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  order_date  TEXT NOT NULL,
  status      TEXT NOT NULL            -- completed | shipped | cancelled
);

CREATE TABLE order_items (
  id         INTEGER PRIMARY KEY,
  order_id   INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity   INTEGER NOT NULL
);
