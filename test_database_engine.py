import os
import tempfile
import unittest

from database_engine import DatabaseEngine


class TestDatabaseEngine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DatabaseEngine(os.path.join(self.temp.name, "project"))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_catalog_and_multiple_tables(self):
        self.assertEqual(
            self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT)"),
            {"message": "Table 'users' created"},
        )
        self.db.execute("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY, title TEXT)")
        self.assertEqual(self.db.execute("SHOW TABLES"), [{"table": "tasks"}, {"table": "users"}])
        self.assertEqual(self.db.execute("DESCRIBE users")[0], {"column": "id", "type": "INT", "primary_key": True})

    def test_table_data_isolated_and_persistent(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
        self.db.execute("CREATE TABLE tasks (task_id INT PRIMARY KEY, title TEXT)")
        self.db.execute("INSERT INTO users (id, name) VALUES (1, 'Ada')")
        self.db.execute("INSERT INTO tasks (task_id, title) VALUES (7, 'Build MiniDB')")
        self.assertEqual(self.db.execute("SELECT * FROM users"), [{"id": 1, "name": "Ada"}])
        self.assertEqual(self.db.execute("SELECT * FROM tasks"), [{"task_id": 7, "title": "Build MiniDB"}])
        path = self.db.directory
        self.db.close()
        self.db = DatabaseEngine(path)
        self.assertEqual(self.db.execute("SELECT name FROM users WHERE id = 1"), [{"name": "Ada"}])

    def test_update_and_delete(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT)")
        self.db.execute("INSERT INTO users (id, name, role) VALUES (1, 'Ada', 'admin')")
        self.assertEqual(self.db.execute("UPDATE users SET role = 'owner' WHERE id = 1"), {"rows_affected": 1})
        self.assertEqual(self.db.execute("SELECT role FROM users WHERE id = 1"), [{"role": "owner"}])
        self.assertEqual(self.db.execute("DELETE FROM users WHERE role = 'owner'"), {"rows_affected": 1})
        self.assertEqual(self.db.execute("SELECT * FROM users"), [])

    def test_select_filters_order_and_limit(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, score INT)")
        self.db.execute("INSERT INTO users (id, name, score) VALUES (1, 'Ada', 82)")
        self.db.execute("INSERT INTO users (id, name, score) VALUES (2, 'Grace', 95)")
        self.db.execute("INSERT INTO users (id, name, score) VALUES (3, 'Linus', 90)")
        self.assertEqual(
            self.db.execute("SELECT name FROM users WHERE score >= 85 AND score < 96 ORDER BY score DESC LIMIT 2"),
            [{"name": "Grace"}, {"name": "Linus"}],
        )

    def test_schema_rejects_incomplete_duplicate_and_wrong_type_inserts(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, score INT)")
        with self.assertRaisesRegex(ValueError, "missing"):
            self.db.execute("INSERT INTO users (id, name) VALUES (1, 'Ada')")
        with self.assertRaisesRegex(ValueError, "unique"):
            self.db.execute("INSERT INTO users (id, id, name, score) VALUES (1, 2, 'Ada', 80)")
        with self.assertRaisesRegex(ValueError, "integer"):
            self.db.execute("INSERT INTO users (id, name, score) VALUES (1, 'Ada', 'high')")
        with self.assertRaisesRegex(ValueError, "unknown column"):
            self.db.execute("INSERT INTO users (id, name, score, email) VALUES (1, 'Ada', 80, 'a@example.com')")

    def test_schema_rejects_unknown_projection_filter_and_mutation_columns(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, score INT)")
        self.db.execute("INSERT INTO users (id, name, score) VALUES (1, 'Ada', 80)")
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            self.db.execute("SELECT emali FROM users")
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            self.db.execute("SELECT id FROM users WHERE missing = 1")
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            self.db.execute("UPDATE users SET score = 90 WHERE missing = 1")
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            self.db.execute("DELETE FROM users WHERE missing = 1")
        with self.assertRaisesRegex(ValueError, "integer"):
            self.db.execute("SELECT id FROM users WHERE id = '1'")

    def test_dashboard_reports_selected_table_schema_and_index(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO users (id, name) VALUES (1, 'Ada')")
        dashboard = self.db.dashboard("users")
        self.assertEqual(dashboard["table"], "users")
        self.assertEqual(dashboard["primary_key"], "id")
        self.assertEqual(dashboard["schema"][0]["column"], "id")
        self.assertEqual(dashboard["indexed_column"], "id")

    def test_sum_rejects_text_columns(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO users (id, name) VALUES (1, 'Ada')")
        with self.assertRaisesRegex(ValueError, "SUM requires an integer"):
            self.db.execute("SELECT SUM(name) FROM users")

    def test_update_values_must_match_column_type(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, score INT)")
        self.db.execute("INSERT INTO users (id, name, score) VALUES (1, 'Ada', 80)")
        with self.assertRaisesRegex(ValueError, "integer"):
            self.db.execute("UPDATE users SET score = 'high' WHERE id = 1")

    def test_aggregates_and_group_by(self):
        self.db.execute("CREATE TABLE users (id INT PRIMARY KEY, role TEXT, score INT)")
        self.db.execute("INSERT INTO users (id, role, score) VALUES (1, 'admin', 80)")
        self.db.execute("INSERT INTO users (id, role, score) VALUES (2, 'user', 90)")
        self.db.execute("INSERT INTO users (id, role, score) VALUES (3, 'user', 100)")
        self.assertEqual(self.db.execute("SELECT COUNT(*) AS total, AVG(score) AS average FROM users"), [{"total": 3, "average": 90.0}])
        self.assertEqual(
            self.db.execute("SELECT role, COUNT(*) AS total FROM users GROUP BY role ORDER BY total DESC"),
            [{"role": "user", "total": 2}, {"role": "admin", "total": 1}],
        )


if __name__ == "__main__":
    unittest.main()
