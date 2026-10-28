"""Interactive multi-table MiniDB command-line shell."""
from pathlib import Path

from database_engine import DatabaseServer


def main() -> None:
    db = DatabaseServer(Path("minidb_cli_catalog"))
    print("MiniDB — enter SQL statements, or .quit")
    print("Try CREATE DATABASE notes; USE notes; SHOW TABLES; .quit")
    try:
        while True:
            sql = input(f"minidb[{db.current_database}{'*' if db.transaction_active else ''}]> ").strip()
            if sql.lower() in {".quit", ".exit"}:
                break
            if not sql:
                continue
            try:
                result = db.execute(sql)
                if isinstance(result, dict) and "statements" in result:
                    for item in result["statements"]:
                        print(f"{item['statement']}\n  => {item['result']}")
                else:
                    print(result)
            except (ValueError, TypeError) as error:
                print(f"Error: {error}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
