"""Load fictional sample records into a separate local MiniDB database.

Run this script before starting web_app.py. The data is written to the ignored
minidb_web_catalog directory and is never included in the public repository.
The operation is safe to repeat: rows with existing IDs are left unchanged.
"""
from __future__ import annotations

from pathlib import Path

from database_engine import DatabaseServer


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "minidb_web_catalog"
DATABASE = "demo_seed"
TABLE = "sample_customers"
SAMPLE_ROWS = (
    (1, "Avery Example", "starter"),
    (2, "Jordan Sample", "team"),
    (3, "Casey Demo", "starter"),
    (4, "Riley Fiction", "enterprise"),
)


def main() -> None:
    server = DatabaseServer(CATALOG, pool_size=8, policy="lru")
    try:
        if DATABASE not in server.names():
            server.create_database(DATABASE)

        engine = server.engine(DATABASE)
        table_schema = engine.catalog["tables"].get(TABLE)
        if table_schema is None:
            server.execute(
                "CREATE TABLE sample_customers "
                "(id INT PRIMARY KEY, name TEXT, plan TEXT)",
                database=DATABASE,
            )
        else:
            columns = [
                (column["name"], column["type"].upper(), column["primary_key"])
                for column in table_schema["columns"]
            ]
            expected = [
                ("id", "INT", True),
                ("name", "TEXT", False),
                ("plan", "TEXT", False),
            ]
            if columns != expected:
                raise ValueError(
                    f"{DATABASE}.{TABLE} already exists with a different schema; "
                    "the sample data was not changed."
                )

        existing_ids = {
            row["id"]
            for row in server.execute(f"SELECT id FROM {TABLE}", database=DATABASE)
        }
        added = 0
        for customer_id, name, plan in SAMPLE_ROWS:
            if customer_id in existing_ids:
                continue
            server.execute(
                "INSERT INTO sample_customers (id, name, plan) "
                f"VALUES ({customer_id}, '{name}', '{plan}')",
                database=DATABASE,
            )
            added += 1

        print(
            f"Demo database '{DATABASE}' is ready: "
            f"{len(SAMPLE_ROWS)} sample IDs, {added} new rows."
        )
        print(f"Local files: {CATALOG / 'databases' / DATABASE}")
        print("Start web_app.py to see the database in the workbench.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
