"""Purposefully small SQL parser for the MiniDB coursework subset."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SelectQuery:
    table: str
    columns: list[str]
    where_column: str | None = None
    where_value: object | None = None
    conditions: list[tuple[str, str, object]] | None = None
    condition_join: str = "AND"
    order_by: str | None = None
    descending: bool = False
    limit: int | None = None
    group_by: str | None = None


@dataclass(frozen=True)
class InsertQuery:
    table: str
    columns: list[str]
    values: list[object]


@dataclass(frozen=True)
class BeginTransactionQuery:
    pass


@dataclass(frozen=True)
class CommitTransactionQuery:
    pass


@dataclass(frozen=True)
class RollbackTransactionQuery:
    pass


@dataclass(frozen=True)
class CreateDatabaseQuery:
    database: str


@dataclass(frozen=True)
class UseDatabaseQuery:
    database: str


@dataclass(frozen=True)
class ShowDatabasesQuery:
    pass


@dataclass(frozen=True)
class CreateTableQuery:
    table: str
    columns: list[tuple[str, str, bool]]  # name, type, primary_key


@dataclass(frozen=True)
class DropTableQuery:
    table: str


@dataclass(frozen=True)
class ShowTablesQuery:
    pass


@dataclass(frozen=True)
class DescribeTableQuery:
    table: str


@dataclass(frozen=True)
class UpdateQuery:
    table: str
    column: str
    value: object
    where_column: str
    where_value: object


@dataclass(frozen=True)
class DeleteQuery:
    table: str
    where_column: str
    where_value: object


class SQLParser:
    SELECT = re.compile(r"^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+GROUP\s+BY\s+(\w+))?(?:\s+ORDER\s+BY\s+(\w+)(?:\s+(ASC|DESC))?)?(?:\s+LIMIT\s+(\d+))?$", re.I)
    INSERT = re.compile(r"^INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)$", re.I)
    TRANSACTION = re.compile(r"^(BEGIN|COMMIT|ROLLBACK)(?:\s+TRANSACTION)?$", re.I)
    CREATE_DATABASE = re.compile(r"^CREATE\s+DATABASE\s+([A-Za-z_]\w*)$", re.I)
    USE_DATABASE = re.compile(r"^USE\s+([A-Za-z_]\w*)$", re.I)
    CREATE = re.compile(r"^CREATE\s+TABLE\s+(\w+)\s*\((.+)\)$", re.I | re.S)
    DROP = re.compile(r"^DROP\s+TABLE\s+(\w+)$", re.I)
    DESCRIBE = re.compile(r"^(?:DESCRIBE|DESC)\s+(\w+)$", re.I)
    UPDATE = re.compile(r"^UPDATE\s+(\w+)\s+SET\s+(\w+)\s*=\s*(.+?)\s+WHERE\s+(\w+)\s*=\s*(.+)$", re.I)
    DELETE = re.compile(r"^DELETE\s+FROM\s+(\w+)\s+WHERE\s+(\w+)\s*=\s*(.+)$", re.I)

    def parse(self, sql: str) -> SelectQuery | InsertQuery | BeginTransactionQuery | CommitTransactionQuery | RollbackTransactionQuery | CreateDatabaseQuery | UseDatabaseQuery | ShowDatabasesQuery | CreateTableQuery | DropTableQuery | ShowTablesQuery | DescribeTableQuery | UpdateQuery | DeleteQuery:
        statement = sql.strip().rstrip(";").strip()
        match = self.TRANSACTION.match(statement)
        if match:
            command = match.group(1).upper()
            return {
                "BEGIN": BeginTransactionQuery,
                "COMMIT": CommitTransactionQuery,
                "ROLLBACK": RollbackTransactionQuery,
            }[command]()
        if re.fullmatch(r"SHOW\s+DATABASES", statement, re.I):
            return ShowDatabasesQuery()
        match = self.CREATE_DATABASE.match(statement)
        if match:
            return CreateDatabaseQuery(match.group(1))
        match = self.USE_DATABASE.match(statement)
        if match:
            return UseDatabaseQuery(match.group(1))
        if re.fullmatch(r"SHOW\s+TABLES", statement, re.I):
            return ShowTablesQuery()
        match = self.DESCRIBE.match(statement)
        if match:
            return DescribeTableQuery(match.group(1))
        match = self.DROP.match(statement)
        if match:
            return DropTableQuery(match.group(1))
        match = self.DELETE.match(statement)
        if match:
            table, where_col, where_value = match.groups()
            return DeleteQuery(table, where_col, self._value(where_value))
        match = self.UPDATE.match(statement)
        if match:
            table, column, value, where_col, where_value = match.groups()
            return UpdateQuery(table, column, self._value(value), where_col, self._value(where_value))
        match = self.CREATE.match(statement)
        if match:
            table, definitions = match.groups()
            columns = []
            for definition in self._split_values(definitions):
                parts = definition.strip().split()
                if len(parts) < 2:
                    raise ValueError("Each column needs a name and data type")
                name, data_type = parts[0], parts[1].upper()
                if data_type not in {"INT", "INTEGER", "TEXT"}:
                    raise ValueError("Supported data types: INT, INTEGER, TEXT")
                primary_key = "PRIMARY KEY" in definition.upper()
                columns.append((name, data_type, primary_key))
            if not columns or len({name for name, _, _ in columns}) != len(columns):
                raise ValueError("Table needs unique column names")
            return CreateTableQuery(table, columns)
        match = self.SELECT.match(statement)
        if match:
            cols, table, where_text, group_by, order_by, direction, limit = match.groups()
            columns = [item.strip() for item in cols.split(",")]
            if not columns or any(not col for col in columns):
                raise ValueError("SELECT needs at least one column")
            if not where_text:
                return SelectQuery(table, columns, order_by=order_by, descending=(direction or "").upper() == "DESC", limit=int(limit) if limit else None, group_by=group_by)
            parts = re.split(r"\s+(AND|OR)\s+", where_text, flags=re.I)
            conditions = []
            joins = []
            for index, part in enumerate(parts):
                if index % 2:
                    joins.append(part.upper())
                    continue
                condition = re.fullmatch(r"(\w+)\s*(=|!=|<>|>=|<=|>|<)\s*(.+)", part.strip())
                if not condition:
                    raise ValueError("WHERE supports column comparisons joined with AND or OR")
                column, operator, value = condition.groups()
                conditions.append((column, operator, self._value(value)))
            if len(set(joins)) > 1:
                raise ValueError("Mixing AND and OR in one WHERE clause is not supported")
            if len(conditions) == 1 and conditions[0][1] == "=":
                column, _, value = conditions[0]
                return SelectQuery(table, columns, column, value, order_by=order_by, descending=(direction or "").upper() == "DESC", limit=int(limit) if limit else None, group_by=group_by)
            return SelectQuery(table, columns, conditions=conditions, condition_join=joins[0] if joins else "AND", order_by=order_by, descending=(direction or "").upper() == "DESC", limit=int(limit) if limit else None, group_by=group_by)
        match = self.INSERT.match(statement)
        if match:
            table, cols, vals = match.groups()
            columns = [item.strip() for item in cols.split(",")]
            values = [self._value(item) for item in self._split_values(vals)]
            if len(columns) != len(values) or not all(columns):
                raise ValueError("INSERT columns and values must match")
            return InsertQuery(table, columns, values)
        raise ValueError("Supported SQL: CREATE DATABASE/TABLE, USE, SHOW DATABASES/TABLES, BEGIN, COMMIT, ROLLBACK, DESCRIBE, SELECT, INSERT, UPDATE, and DELETE")

    @staticmethod
    def _split_values(values: str) -> list[str]:
        return re.split(r"\s*,\s*(?=(?:[^']*'[^']*')*[^']*$)", values)

    @staticmethod
    def _value(raw: str) -> object:
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            return raw[1:-1]
        try:
            return int(raw)
        except ValueError:
            return raw
