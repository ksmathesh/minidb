"""One-command MiniDB portfolio demonstration."""
from __future__ import annotations

import os
import tempfile

from database import MiniDB


def main() -> None:
    path = os.path.join(tempfile.gettempdir(), "minidb_portfolio_demo.db")
    if os.path.exists(path):
        os.remove(path)
    db = MiniDB(path, pool_size=3, policy="lru")
    try:
        for sql in (
            "INSERT INTO users (id, name, role) VALUES (1, 'Ada', 'admin')",
            "INSERT INTO users (id, name, role) VALUES (2, 'Grace', 'engineer')",
            "INSERT INTO users (id, name, role) VALUES (3, 'Linus', 'engineer')",
        ):
            db.execute(sql)
        db.buffer_pool.reset_metrics()
        print("Indexed lookup:", db.execute("SELECT name FROM users WHERE id = 2"))
        print("Scan fallback:", db.execute("SELECT id, name FROM users WHERE role = 'engineer'"))
        print("Engine state:", db.dashboard())
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    main()
