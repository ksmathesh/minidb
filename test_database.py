import os
import tempfile
import unittest

from database import MiniDB
from bplustree import BPlusTree, RowPointer
from sql_parser import SQLParser, SelectQuery


class TestMiniDB(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "mini.db")
        self.db = MiniDB(self.path, pool_size=2, policy="lru")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_parser_select(self):
        q = SQLParser().parse("SELECT name FROM users WHERE id = 3;")
        self.assertEqual(q, SelectQuery("users", ["name"], "id", 3))

    def test_index_and_scan_paths(self):
        self.db.execute("INSERT INTO users (id, name, role) VALUES (1, 'Ada', 'admin')")
        self.db.execute("INSERT INTO users (id, name, role) VALUES (2, 'Linus', 'user')")
        self.db.buffer_pool.reset_metrics()
        self.assertEqual(self.db.execute("SELECT name FROM users WHERE id = 1"), [{"name": "Ada"}])
        self.assertEqual(self.db.buffer_pool.hits + self.db.buffer_pool.misses, 1)
        self.assertEqual(self.db.execute("SELECT id FROM users WHERE role = 'user'"), [{"id": 2}])

    def test_bplus_tree_range_search_across_leaf_splits(self):
        tree = BPlusTree(order=3)
        for key in range(20):
            tree.insert(key, RowPointer(key, key + 1))

        self.assertEqual(
            tree.range_search(5, 10),
            [RowPointer(key, key + 1) for key in range(5, 11)],
        )
        self.assertEqual(
            tree.range_search(5, 10, include_lower=False, include_upper=False),
            [RowPointer(key, key + 1) for key in range(6, 10)],
        )
        self.assertEqual(tree.range_search(8, 8, include_lower=False), [])
        self.assertEqual(
            tree.range_search(lower=18),
            [RowPointer(key, key + 1) for key in range(18, 20)],
        )

    def test_indexed_range_query(self):
        for key in range(1, 8):
            self.db.execute(
                f"INSERT INTO users (id, name) VALUES ({key}, 'user-{key}')"
            )
        self.assertEqual(
            self.db.execute("SELECT id FROM users WHERE id > 2 AND id <= 5 ORDER BY id"),
            [{"id": 3}, {"id": 4}, {"id": 5}],
        )
        self.assertEqual(
            self.db.execute("SELECT id FROM users WHERE id >= 5 AND id = 2"),
            [],
        )

    def test_persistence_rebuilds_index(self):
        self.db.execute("INSERT INTO users (id, name) VALUES (9, 'Grace')")
        self.db.close()
        self.db = MiniDB(self.path)
        self.assertEqual(self.db.execute("SELECT * FROM users WHERE id = 9"), [{"id": 9, "name": "Grace"}])

    def test_bplus_tree_survives_leaf_and_internal_splits(self):
        tree = BPlusTree(order=3)
        for key in range(20):
            tree.insert(key, RowPointer(key, key + 1))
        self.assertEqual(tree.search(14), RowPointer(14, 15))
        self.assertIsNone(tree.search(99))


if __name__ == "__main__":
    unittest.main()
