"""Single-table MiniDB: pages, buffer pool, B+ tree index, parser and executor."""
from __future__ import annotations

import json
import re
from pathlib import Path

from bplustree import BPlusTree, RowPointer
from buffer_pool_manager import BufferPoolManager
from disk_manager import DiskManager
from sql_parser import DeleteQuery, InsertQuery, SQLParser, SelectQuery, UpdateQuery


class MiniDB:
    def __init__(self, filename: str | Path, pool_size: int = 8, policy: str = "lru", indexed_column: str = "id"):
        self.disk = DiskManager(str(filename))
        self.buffer_pool = BufferPoolManager(pool_size, self.disk, policy)
        self.indexed_column = indexed_column
        self.index = BPlusTree()
        self.parser = SQLParser()
        self._rebuild_index()

    def close(self) -> None:
        self.buffer_pool.flush_all_pages()
        self.disk.close()

    def execute(self, sql: str):
        query = self.parser.parse(sql)
        if isinstance(query, InsertQuery):
            return self._insert(query)
        if isinstance(query, UpdateQuery):
            return self._update(query)
        if isinstance(query, DeleteQuery):
            return self._delete(query)
        return self._select(query)

    def dashboard(self) -> dict:
        """Read-only engine details used by the local demonstration frontend."""
        bpm = self.buffer_pool
        return {
            "page_count": self.disk.num_pages,
            "page_size": 4096,
            "indexed_column": self.indexed_column,
            "index_levels": self.index.levels(),
            "frame_pages": bpm.frame_page_ids[:],
            "pool_size": bpm.pool_size,
            "dirty_pages": sum(bpm.is_dirty),
            "metrics": {
                "policy": bpm.policy_name,
                "hits": bpm.hits,
                "misses": bpm.misses,
                "hit_rate_percent": bpm.get_hit_rate() * 100,
            },
        }

    def _insert(self, query: InsertQuery) -> RowPointer:
        row = dict(zip(query.columns, query.values, strict=True))
        if self.indexed_column not in row:
            raise ValueError(f"INSERT must include indexed column '{self.indexed_column}'")
        if self.index.search(row[self.indexed_column]) is not None:
            raise ValueError("duplicate indexed key")
        payload = json.dumps(row, separators=(",", ":")).encode("utf-8")
        pointer = self._append(payload)
        self.index.insert(row[self.indexed_column], pointer)
        return pointer

    def _append(self, payload: bytes) -> RowPointer:
        for page_id in range(self.disk.num_pages):
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            slot_id = page.insert_record(payload)
            self.buffer_pool.unpin_page(page_id, is_dirty=slot_id >= 0)
            if slot_id >= 0:
                return RowPointer(page_id, slot_id)
        page_id = self.disk.allocate_page()
        page = self.buffer_pool.fetch_page(page_id)
        assert page is not None
        slot_id = page.insert_record(payload)
        self.buffer_pool.unpin_page(page_id, is_dirty=True)
        return RowPointer(page_id, slot_id)

    def _select(self, query: SelectQuery) -> list[dict]:
        if query.where_column == self.indexed_column:
            pointer = self.index.search(query.where_value)
            rows = [self._read_pointer(pointer)] if pointer else []
        elif query.conditions and query.condition_join == "AND" and any(
            column == self.indexed_column and operator in {"=", ">", ">=", "<", "<="}
            for column, operator, _value in query.conditions
        ):
            bounds = self._index_bounds(query.conditions)
            pointers = self.index.range_search(
                bounds["lower"], bounds["upper"],
                include_lower=bounds["include_lower"],
                include_upper=bounds["include_upper"],
            )
            rows = [self._read_pointer(pointer) for pointer in pointers]
            rows = [row for row in rows if self._matches_conditions(row, query.conditions, "AND")]
        else:
            rows = list(self._scan())
            if query.where_column is not None:
                rows = [row for row in rows if row.get(query.where_column) == query.where_value]
            elif query.conditions:
                rows = [row for row in rows if self._matches_conditions(row, query.conditions, query.condition_join)]
        rows = [row for row in rows if row is not None]
        if any(self._aggregate_spec(column) for column in query.columns):
            result_rows = self._aggregate_rows(rows, query.columns, query.group_by)
            if query.order_by:
                result_rows.sort(key=lambda row: (row.get(query.order_by) is None, row.get(query.order_by)), reverse=query.descending)
            return result_rows[:query.limit] if query.limit is not None else result_rows
        if query.order_by:
            rows.sort(key=lambda row: (row.get(query.order_by) is None, row.get(query.order_by)), reverse=query.descending)
        if query.limit is not None:
            rows = rows[:query.limit]
        return [row if query.columns == ["*"] else {col: row.get(col) for col in query.columns} for row in rows]

    def _index_bounds(self, conditions: list[tuple[str, str, object]]) -> dict:
        lower = upper = None
        include_lower = include_upper = True
        for column, operator, value in conditions:
            if column != self.indexed_column:
                continue
            if operator in {"=", ">", ">="}:
                candidate = value
                inclusive = operator != ">"
                if lower is None or candidate > lower or (candidate == lower and not inclusive):
                    lower, include_lower = candidate, inclusive
                if operator == "=":
                    if upper is None or candidate < upper or (candidate == upper and not include_upper):
                        upper, include_upper = candidate, True
            elif operator in {"<", "<="}:
                inclusive = operator == "<="
                if upper is None or value < upper or (value == upper and not inclusive):
                    upper, include_upper = value, inclusive
        return {
            "lower": lower, "upper": upper,
            "include_lower": include_lower, "include_upper": include_upper,
        }

    @staticmethod
    def _aggregate_spec(column: str) -> tuple[str, str, str] | None:
        match = re.fullmatch(r"(COUNT|SUM|AVG|MIN|MAX)\((\*|\w+)\)(?:\s+AS\s+(\w+))?", column, re.I)
        if not match:
            return None
        function, source, alias = match.groups()
        return function.upper(), source, alias or f"{function.upper()}({source})"

    def _aggregate_rows(self, rows: list[dict], columns: list[str], group_by: str | None) -> list[dict]:
        groups: dict[object, list[dict]] = {}
        if group_by:
            for row in rows:
                groups.setdefault(row.get(group_by), []).append(row)
        else:
            groups[None] = rows
        result = []
        for key, members in groups.items():
            item = {}
            for column in columns:
                aggregate = self._aggregate_spec(column)
                if not aggregate:
                    if column == group_by:
                        item[column] = key
                    else:
                        raise ValueError("Non-aggregate SELECT columns must appear in GROUP BY")
                    continue
                function, source, alias = aggregate
                values = [row.get(source) for row in members] if source != "*" else []
                values = [value for value in values if value is not None]
                if function == "COUNT":
                    item[alias] = len(members) if source == "*" else len(values)
                elif function == "SUM":
                    item[alias] = sum(values) if values else 0
                elif function == "AVG":
                    item[alias] = sum(values) / len(values) if values else None
                elif function == "MIN":
                    item[alias] = min(values) if values else None
                elif function == "MAX":
                    item[alias] = max(values) if values else None
            result.append(item)
        return result

    @staticmethod
    def _matches_conditions(row: dict, conditions: list[tuple[str, str, object]], join: str) -> bool:
        def matches(condition: tuple[str, str, object]) -> bool:
            column, operator, value = condition
            current = row.get(column)
            if operator == "=":
                return current == value
            if operator in {"!=", "<>"}:
                return current != value
            if current is None:
                return False
            return {">": current > value, "<": current < value, ">=": current >= value, "<=": current <= value}[operator]

        values = [matches(condition) for condition in conditions]
        return any(values) if join == "OR" else all(values)

    def _delete(self, query: DeleteQuery) -> dict:
        deleted = 0
        for page_id, slot_id, row in list(self._matching_rows(query.where_column, query.where_value)):
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            if page.delete_record(slot_id):
                deleted += 1
                self.buffer_pool.unpin_page(page_id, is_dirty=True)
            else:
                self.buffer_pool.unpin_page(page_id)
        if deleted:
            self.index = BPlusTree()
            self._rebuild_index()
        return {"rows_affected": deleted}

    def _update(self, query: UpdateQuery) -> dict:
        if query.column == self.indexed_column:
            raise ValueError("Updating a primary key is not supported")
        updated = 0
        for page_id, slot_id, row in list(self._matching_rows(query.where_column, query.where_value)):
            row[query.column] = query.value
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            page.delete_record(slot_id)
            self.buffer_pool.unpin_page(page_id, is_dirty=True)
            self._append(json.dumps(row, separators=(",", ":")).encode("utf-8"))
            updated += 1
        if updated:
            self.index = BPlusTree()
            self._rebuild_index()
        return {"rows_affected": updated}

    def _matching_rows(self, column: str, value: object):
        for page_id in range(self.disk.num_pages):
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            rows = []
            for slot_id in range(page.num_slots):
                data = page.get_record(slot_id)
                if data is not None:
                    row = json.loads(data)
                    if row.get(column) == value:
                        rows.append((page_id, slot_id, row))
            self.buffer_pool.unpin_page(page_id)
            yield from rows

    def _read_pointer(self, pointer: RowPointer) -> dict | None:
        page = self.buffer_pool.fetch_page(pointer.page_id)
        assert page is not None
        data = page.get_record(pointer.slot_id)
        self.buffer_pool.unpin_page(pointer.page_id)
        return json.loads(data) if data is not None else None

    def _scan(self):
        for page_id in range(self.disk.num_pages):
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            for slot_id in range(page.num_slots):
                data = page.get_record(slot_id)
                if data is not None:
                    yield json.loads(data)
            self.buffer_pool.unpin_page(page_id)

    def _rebuild_index(self) -> None:
        for page_id in range(self.disk.num_pages):
            page = self.buffer_pool.fetch_page(page_id)
            assert page is not None
            for slot_id in range(page.num_slots):
                data = page.get_record(slot_id)
                if data:
                    row = json.loads(data)
                    if self.indexed_column in row:
                        self.index.insert(row[self.indexed_column], RowPointer(page_id, slot_id))
            self.buffer_pool.unpin_page(page_id)
        self.buffer_pool.reset_metrics()
