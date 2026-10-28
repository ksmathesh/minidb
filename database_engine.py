"""Multi-table catalog and SQL dispatcher built on top of MiniDB table files."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import os
from pathlib import Path

from database import MiniDB
from sql_parser import (
    BeginTransactionQuery,
    CommitTransactionQuery,
    RollbackTransactionQuery,
    CreateDatabaseQuery,
    UseDatabaseQuery,
    ShowDatabasesQuery,
    CreateTableQuery,
    DescribeTableQuery,
    DropTableQuery,
    InsertQuery,
    SQLParser,
    SelectQuery,
    ShowTablesQuery,
    UpdateQuery,
    DeleteQuery,
)


class DatabaseEngine:
    """A small persistent relational database with one MiniDB file per table."""

    def __init__(self, directory: str | Path, pool_size: int = 8, policy: str = "lru"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.directory / "catalog.json"
        self.pool_size = pool_size
        self.policy = policy
        self.parser = SQLParser()
        self.tables: dict[str, MiniDB] = {}
        self.catalog = self._load_catalog()

    def execute(self, sql: str):
        query = self.parser.parse(sql)
        if isinstance(query, (CreateDatabaseQuery, UseDatabaseQuery, ShowDatabasesQuery)):
            raise ValueError("Database commands need a DatabaseServer session")
        if isinstance(query, CreateTableQuery):
            return self._create_table(query)
        if isinstance(query, DropTableQuery):
            return self._drop_table(query.table)
        if isinstance(query, ShowTablesQuery):
            return [{"table": name} for name in sorted(self.catalog["tables"])]
        if isinstance(query, DescribeTableQuery):
            return self._describe(query.table)
        if isinstance(query, (SelectQuery, InsertQuery, UpdateQuery, DeleteQuery)):
            table = self._table(query.table)
            if isinstance(query, InsertQuery):
                self._validate_insert(query)
            elif isinstance(query, SelectQuery):
                self._validate_select(query)
            elif isinstance(query, UpdateQuery):
                self._validate_column(query.table, query.column)
                self._validate_value(query.table, query.column, query.value)
                self._validate_filter(query.table, query.where_column, query.where_value)
                if query.column == self.catalog["tables"][query.table]["primary_key"]:
                    raise ValueError("Updating a primary key is not supported")
            elif isinstance(query, DeleteQuery):
                self._validate_filter(query.table, query.where_column, query.where_value)
            return table.execute(sql)
        raise ValueError("Unsupported statement")

    def close(self) -> None:
        for table in self.tables.values():
            table.close()
        self.tables.clear()

    def dashboard(self, table_name: str) -> dict:
        """Return read-only storage and index details for a catalog table."""
        table = self._table(table_name)
        details = table.dashboard()
        details.update({
            "table": table_name,
            "primary_key": self.catalog["tables"][table_name]["primary_key"],
            "schema": self._describe(table_name),
        })
        return details

    def _load_catalog(self) -> dict:
        if not self.catalog_path.exists():
            catalog = {"tables": {}}
            self._save_catalog(catalog)
            return catalog
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def _save_catalog(self, catalog: dict | None = None) -> None:
        """Replace the catalog atomically so interruption cannot leave partial JSON."""
        content = json.dumps(catalog or self.catalog, indent=2)
        temp_path = self.catalog_path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as catalog_file:
            catalog_file.write(content)
            catalog_file.flush()
            os.fsync(catalog_file.fileno())
        os.replace(temp_path, self.catalog_path)

    def _create_table(self, query: CreateTableQuery) -> dict:
        self._validate_name(query.table)
        if query.table in self.catalog["tables"]:
            raise ValueError(f"Table '{query.table}' already exists")
        primary = [name for name, _, is_primary in query.columns if is_primary]
        if len(primary) != 1:
            raise ValueError("Exactly one PRIMARY KEY column is required")
        schema = [{"name": name, "type": data_type, "primary_key": is_primary} for name, data_type, is_primary in query.columns]
        self.catalog["tables"][query.table] = {"columns": schema, "primary_key": primary[0]}
        self._save_catalog()
        self._table(query.table)
        return {"message": f"Table '{query.table}' created"}

    def _drop_table(self, name: str) -> dict:
        self._validate_name(name)
        if name not in self.catalog["tables"]:
            raise ValueError(f"Table '{name}' does not exist")
        table = self.tables.pop(name, None)
        if table:
            table.close()
        table_path = self._table_path(name)
        if table_path.exists():
            table_path.unlink()
        del self.catalog["tables"][name]
        self._save_catalog()
        return {"message": f"Table '{name}' dropped"}

    def _describe(self, name: str) -> list[dict]:
        if name not in self.catalog["tables"]:
            raise ValueError(f"Table '{name}' does not exist")
        return [{"column": col["name"], "type": col["type"], "primary_key": col["primary_key"]} for col in self.catalog["tables"][name]["columns"]]

    def _table(self, name: str) -> MiniDB:
        self._validate_name(name)
        if name not in self.catalog["tables"]:
            raise ValueError(f"Table '{name}' does not exist")
        if name not in self.tables:
            primary_key = self.catalog["tables"][name]["primary_key"]
            self.tables[name] = MiniDB(self._table_path(name), self.pool_size, self.policy, primary_key)
        return self.tables[name]

    def _validate_insert(self, query: InsertQuery) -> None:
        schema = self.catalog["tables"][query.table]["columns"]
        allowed = {column["name"] for column in schema}
        primary = self.catalog["tables"][query.table]["primary_key"]
        if len(query.columns) != len(set(query.columns)):
            raise ValueError("INSERT column names must be unique")
        unknown = set(query.columns) - allowed
        if unknown:
            raise ValueError(f"INSERT contains unknown column(s): {', '.join(sorted(unknown))}")
        missing = allowed - set(query.columns)
        if missing:
            raise ValueError(f"INSERT must provide every column; missing: {', '.join(sorted(missing))}")
        if primary not in query.columns:
            raise ValueError(f"INSERT must include primary key '{primary}'")
        values = dict(zip(query.columns, query.values, strict=True))
        for column in schema:
            self._validate_value(query.table, column["name"], values[column["name"]])

    def _validate_select(self, query: SelectQuery) -> None:
        aggregates = []
        plain_columns = []
        if query.columns == ["*"]:
            if query.group_by:
                raise ValueError("SELECT * cannot be used with GROUP BY")
        elif "*" in query.columns:
            raise ValueError("SELECT * cannot be combined with other columns")
        else:
            for expression in query.columns:
                spec = MiniDB._aggregate_spec(expression)
                if spec is None:
                    self._validate_column(query.table, expression)
                    plain_columns.append(expression)
                else:
                    function, source, alias = spec
                    if source == "*" and function != "COUNT":
                        raise ValueError(f"{function}(*) is not supported")
                    if source != "*":
                        self._validate_column(query.table, source)
                        if function in {"SUM", "AVG"}:
                            source_type = next(
                                item["type"].upper()
                                for item in self.catalog["tables"][query.table]["columns"]
                                if item["name"] == source
                            )
                            if source_type not in {"INT", "INTEGER"}:
                                raise ValueError(f"{function} requires an integer column")
                    aggregates.append((function, alias))

        if query.group_by:
            self._validate_column(query.table, query.group_by)
            if not aggregates:
                raise ValueError("GROUP BY requires at least one aggregate expression")
            if any(column != query.group_by for column in plain_columns):
                raise ValueError("Non-aggregate SELECT columns must appear in GROUP BY")
        elif aggregates and plain_columns:
            raise ValueError("Non-aggregate SELECT columns must appear in GROUP BY")

        if query.where_column is not None:
            self._validate_filter(query.table, query.where_column, query.where_value)
        for column, _operator, value in query.conditions or []:
            self._validate_filter(query.table, column, value)

        aggregate_aliases = {alias for _function, alias in aggregates}
        if query.order_by and query.order_by not in aggregate_aliases:
            self._validate_column(query.table, query.order_by)

    def _validate_filter(self, table: str, column: str, value: object) -> None:
        self._validate_column(table, column)
        self._validate_value(table, column, value)

    def _validate_column(self, table: str, column: str) -> None:
        allowed = {item["name"] for item in self.catalog["tables"][table]["columns"]}
        if column not in allowed:
            raise ValueError(f"Unknown column '{column}' in table '{table}'")

    def _validate_value(self, table: str, column: str, value: object) -> None:
        definition = next(
            item for item in self.catalog["tables"][table]["columns"] if item["name"] == column
        )
        data_type = definition["type"].upper()
        valid = (
            isinstance(value, int) and not isinstance(value, bool)
            if data_type in {"INT", "INTEGER"}
            else isinstance(value, str)
        )
        if not valid:
            expected = "integer" if data_type in {"INT", "INTEGER"} else "text"
            raise ValueError(f"Column '{column}' requires a {expected} value")

    def _table_path(self, name: str) -> Path:
        return self.directory / f"{name}.db"

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError("Table names must contain letters, digits, and underscores only")


class DatabaseServer:
    """Database/session layer for CREATE DATABASE, SHOW DATABASES and USE."""

    def __init__(self, directory: str | Path, pool_size: int = 8, policy: str = "lru"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pool_size = pool_size
        self.policy = policy
        self.current_database = "default"
        self.databases: dict[str, DatabaseEngine] = {}
        self._transaction_database: str | None = None
        self._transaction_temp: tempfile.TemporaryDirectory | None = None
        self.engine("default")
        child_root = self.directory / "databases"
        if child_root.exists():
            for child in child_root.iterdir():
                if child.is_dir() and (child / "catalog.json").exists():
                    self.engine(child.name)

    @property
    def transaction_active(self) -> bool:
        return self._transaction_database is not None

    @property
    def transaction_database(self) -> str | None:
        return self._transaction_database

    def names(self) -> list[str]:
        return sorted(self.databases)

    @staticmethod
    def _positions(sql: str, statements: list[str]) -> list[int]:
        positions = []
        cursor = 0
        for statement in statements:
            position = sql.find(statement, cursor)
            if position < 0:
                position = cursor
            positions.append(position)
            cursor = position + len(statement)
        return positions

    @staticmethod
    def _line_column(sql: str, offset: int) -> tuple[int, int]:
        line = sql.count("\n", 0, offset) + 1
        previous_newline = sql.rfind("\n", 0, offset)
        return line, offset - previous_newline

    @classmethod
    def _error_location(cls, sql: str, statement: str, offset: int, error: Exception) -> tuple[int, int]:
        relative = 0
        for token in re.findall(r"'([^']+)'", str(error)):
            candidate = statement.find(token)
            if candidate >= 0:
                relative = candidate
                break
        return cls._line_column(sql, offset + relative)

    def engine(self, name: str | None = None) -> DatabaseEngine:
        name = name or self.current_database
        DatabaseEngine._validate_name(name)
        if name not in self.databases:
            if name.casefold() in {current.casefold() for current in self.databases}:
                raise ValueError(f"Database name '{name}' differs only by letter case from an existing database")
            path = self.directory if name == "default" else self.directory / "databases" / name
            self.databases[name] = DatabaseEngine(path, self.pool_size, self.policy)
        return self.databases[name]

    def create_database(self, name: str) -> dict:
        DatabaseEngine._validate_name(name)
        if name.casefold() in {current.casefold() for current in self.databases}:
            raise ValueError(f"Database '{name}' already exists")
        self.engine(name)
        return {"message": f"Database '{name}' created"}

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """Split a script on semicolons outside quoted strings."""
        statements: list[str] = []
        current: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(sql):
            char = sql[index]
            if quote:
                current.append(char)
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        current.append(sql[index + 1])
                        index += 1
                    else:
                        quote = None
            elif char in {"'", '"'}:
                quote = char
                current.append(char)
            elif char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current.clear()
            else:
                current.append(char)
            index += 1
        if quote:
            raise ValueError("Unterminated quoted string")
        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        return statements

    def _begin(self, database: str) -> dict:
        if self._transaction_database is not None:
            raise ValueError("A transaction is already active")
        engine = self.engine(database)
        engine.close()  # Flush dirty pages before capturing the rollback point.
        self._transaction_temp = tempfile.TemporaryDirectory(prefix="minidb_txn_")
        snapshot = Path(self._transaction_temp.name)
        for item in engine.directory.iterdir():
            if item.is_file() and (item.name == "catalog.json" or item.suffix == ".db"):
                shutil.copy2(item, snapshot / item.name)
        self._transaction_database = database
        return {"message": f"Transaction started on database '{database}'"}

    def _finish_transaction(self, commit: bool) -> dict:
        database = self._transaction_database
        if database is None or self._transaction_temp is None:
            raise ValueError("No active transaction")
        engine = self.engine(database)
        engine.close()
        if not commit:
            snapshot = Path(self._transaction_temp.name)
            for item in engine.directory.iterdir():
                if item.is_file() and (item.name == "catalog.json" or item.suffix == ".db"):
                    item.unlink()
            for item in snapshot.iterdir():
                shutil.copy2(item, engine.directory / item.name)
            self.databases.pop(database, None)
        self._transaction_temp.cleanup()
        self._transaction_temp = None
        self._transaction_database = None
        if not commit:
            self.engine(database)
            self.current_database = database
            return {"message": f"Transaction rolled back on database '{database}'"}
        return {"message": f"Transaction committed on database '{database}'"}

    def execute(self, sql: str, database: str | None = None):
        try:
            statements = self._split_statements(sql)
        except ValueError as error:
            offset = max(sql.rfind("'"), sql.rfind('"')) if "Unterminated quoted string" in str(error) else 0
            line, column = self._line_column(sql, offset)
            if self._transaction_database is not None:
                try:
                    self._finish_transaction(commit=False)
                except Exception as rollback_error:
                    raise ValueError(f"SQL error at line {line}, column {column}: {error}; rollback also failed: {rollback_error}") from error
            raise ValueError(f"SQL error at line {line}, column {column}: {error}") from error
        if not statements:
            raise ValueError("Enter at least one SQL statement")
        positions = self._positions(sql, statements)
        if database is not None:
            if self._transaction_database and database != self._transaction_database:
                raise ValueError(f"Transaction is active on database '{self._transaction_database}'")
            self.current_database = database
        active = database or self.current_database
        results = []
        for index, statement in enumerate(statements, start=1):
            try:
                query = SQLParser().parse(statement)
                if isinstance(query, ShowDatabasesQuery):
                    result = [{"database": name} for name in self.names()]
                elif isinstance(query, CreateDatabaseQuery):
                    if self._transaction_database:
                        raise ValueError("CREATE DATABASE is not supported inside a transaction")
                    result = self.create_database(query.database)
                elif isinstance(query, UseDatabaseQuery):
                    if self._transaction_database and query.database != self._transaction_database:
                        raise ValueError(f"Transaction is active on database '{self._transaction_database}'")
                    if query.database not in self.databases:
                        raise ValueError(f"Database '{query.database}' does not exist; use CREATE DATABASE first")
                    active = query.database
                    self.current_database = active
                    result = {"message": f"Using database '{query.database}'", "database": query.database}
                elif isinstance(query, BeginTransactionQuery):
                    result = self._begin(active)
                elif isinstance(query, CommitTransactionQuery):
                    result = self._finish_transaction(commit=True)
                elif isinstance(query, RollbackTransactionQuery):
                    result = self._finish_transaction(commit=False)
                    active = self.current_database
                else:
                    if self._transaction_database and active != self._transaction_database:
                        raise ValueError(f"Transaction is active on database '{self._transaction_database}'")
                    result = self.engine(active).execute(statement)
                results.append({"statement": statement, "result": result})
            except Exception as error:
                line, column = self._error_location(sql, statement, positions[index - 1], error)
                source_line = sql.splitlines()[line - 1] if sql.splitlines() and line <= len(sql.splitlines()) else statement
                location = f"Statement {index} failed at line {line}, column {column}: {error}\n{source_line}\n{' ' * (column - 1)}^"
                if self._transaction_database is not None:
                    try:
                        self._finish_transaction(commit=False)
                    except Exception as rollback_error:
                        raise ValueError(f"{location}\nRollback also failed: {rollback_error}") from error
                raise ValueError(location) from error
        if len(results) == 1:
            return results[0]["result"]
        return {"statements": results, "database": active}

    def close(self) -> None:
        if self._transaction_database is not None:
            self._finish_transaction(commit=False)
        for engine in self.databases.values():
            engine.close()
        self.databases.clear()
