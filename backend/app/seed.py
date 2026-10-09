"""Create a small sample database. Run: python -m app.seed"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import settings

SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")


def seed(path: str | None = None) -> None:
    db_path = Path(path or settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    rnd = random.Random(42)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    countries = ["USA", "UK", "India", "Germany", "Brazil", "Japan"]
    first = ["Ava", "Liam", "Noah", "Mia", "Zoe", "Ravi", "Anika", "Chen", "Lena", "Omar"]
    last = ["Smith", "Patel", "Müller", "Silva", "Tanaka", "Brown", "Khan", "Jones"]
    start = date(2023, 1, 1)
    conn.executemany(
        "INSERT INTO customers VALUES (?,?,?,?)",
        [
            (i, f"{rnd.choice(first)} {rnd.choice(last)}", rnd.choice(countries),
             (start + timedelta(days=rnd.randint(0, 600))).isoformat())
            for i in range(1, 101)
        ],
    )
    cats = {"Electronics": 150, "Books": 20, "Home": 60, "Sports": 80, "Toys": 30}
    products = []
    pid = 1
    for cat, base in cats.items():
        for n in range(1, 7):
            products.append((pid, f"{cat} Item {n}", cat, round(base * rnd.uniform(0.5, 1.8), 2)))
            pid += 1
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    statuses = ["completed", "completed", "completed", "shipped", "cancelled"]
    item_id = 1
    for oid in range(1, 401):
        conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?)",
            (oid, rnd.randint(1, 100),
             (date(2024, 1, 1) + timedelta(days=rnd.randint(0, 600))).isoformat(),
             rnd.choice(statuses)),
        )
        for _ in range(rnd.randint(1, 4)):
            conn.execute(
                "INSERT INTO order_items VALUES (?,?,?,?)",
                (item_id, oid, rnd.randint(1, len(products)), rnd.randint(1, 5)),
            )
            item_id += 1
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print(f"Seeded {settings.database_path}")
