"""Small in-memory B+ tree used to map indexed keys to (page_id, slot_id)."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RowPointer:
    page_id: int
    slot_id: int


@dataclass
class _Node:
    leaf: bool
    keys: list[object] = field(default_factory=list)
    values: list[RowPointer] = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)
    next_leaf: "_Node | None" = None


class BPlusTree:
    """B+ tree for unique keys. Leaf nodes contain row pointers."""
    def __init__(self, order: int = 4):
        if order < 3:
            raise ValueError("B+ tree order must be at least 3")
        self.order = order
        self.root = _Node(leaf=True)

    def search(self, key: object) -> RowPointer | None:
        node = self.root
        while not node.leaf:
            index = 0
            while index < len(node.keys) and key >= node.keys[index]:
                index += 1
            node = node.children[index]
        try:
            return node.values[node.keys.index(key)]
        except ValueError:
            return None

    def insert(self, key: object, pointer: RowPointer) -> None:
        split = self._insert(self.root, key, pointer)
        if split is not None:
            separator, right = split
            self.root = _Node(leaf=False, keys=[separator], children=[self.root, right])

    def _insert(self, node: _Node, key: object, pointer: RowPointer):
        if node.leaf:
            position = 0
            while position < len(node.keys) and node.keys[position] < key:
                position += 1
            if position < len(node.keys) and node.keys[position] == key:
                node.values[position] = pointer
                return None
            node.keys.insert(position, key)
            node.values.insert(position, pointer)
            if len(node.keys) < self.order:
                return None
            midpoint = len(node.keys) // 2
            right = _Node(leaf=True, keys=node.keys[midpoint:], values=node.values[midpoint:])
            right.next_leaf = node.next_leaf
            node.next_leaf = right
            node.keys, node.values = node.keys[:midpoint], node.values[:midpoint]
            return right.keys[0], right

        child_index = 0
        while child_index < len(node.keys) and key >= node.keys[child_index]:
            child_index += 1
        split = self._insert(node.children[child_index], key, pointer)
        if split is None:
            return None
        separator, right = split
        node.keys.insert(child_index, separator)
        node.children.insert(child_index + 1, right)
        if len(node.keys) < self.order:
            return None
        midpoint = len(node.keys) // 2
        promote = node.keys[midpoint]
        right = _Node(leaf=False, keys=node.keys[midpoint + 1:], children=node.children[midpoint + 1:])
        node.keys, node.children = node.keys[:midpoint], node.children[:midpoint + 1]
        return promote, right

    def _find_leaf(self, key: object) -> _Node:
        node = self.root
        while not node.leaf:
            child_index = 0
            while child_index < len(node.keys) and key >= node.keys[child_index]:
                child_index += 1
            node = node.children[child_index]
        return node

    def range_search(
        self,
        lower: object | None = None,
        upper: object | None = None,
        *,
        include_lower: bool = True,
        include_upper: bool = True,
    ) -> list[RowPointer]:
        """Return pointers for keys within the requested ordered range."""
        if lower is not None and upper is not None:
            if lower > upper or (lower == upper and not (include_lower and include_upper)):
                return []

        if lower is None:
            leaf = self.root
            while not leaf.leaf:
                leaf = leaf.children[0]
            index = 0
        else:
            leaf = self._find_leaf(lower)
            index = bisect_left(leaf.keys, lower)
            if not include_lower and index < len(leaf.keys) and leaf.keys[index] == lower:
                index += 1

        results: list[RowPointer] = []
        while leaf is not None:
            while index < len(leaf.keys):
                key = leaf.keys[index]
                if upper is not None and (key > upper or (key == upper and not include_upper)):
                    return results
                results.append(leaf.values[index])
                index += 1
            leaf = leaf.next_leaf
            index = 0
        return results

    def levels(self) -> list[list[list[object]]]:
        """Return the index keys grouped by depth for the read-only frontend."""
        result: list[list[list[object]]] = []
        frontier = [self.root]
        while frontier:
            result.append([node.keys[:] for node in frontier])
            frontier = [child for node in frontier if not node.leaf for child in node.children]
        return result
