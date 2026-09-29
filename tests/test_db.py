"""The SQLite layer: migrations, transactions and the write lock."""
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from hah import config, db
from tests.support import ServerTestCase


class TempDbTest(unittest.TestCase):
    """Each test gets its own database file and (optionally) migrations folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._db, self._mig = config.DB_PATH, db.MIGRATIONS
        config.DB_PATH = os.path.join(self.dir, "t.db")

    def tearDown(self):
        config.DB_PATH, db.MIGRATIONS = self._db, self._mig
        shutil.rmtree(self.dir, ignore_errors=True)

    def use_migrations(self, files):
        mig = os.path.join(self.dir, "migrations")
        os.makedirs(mig, exist_ok=True)
        for name, sql in files.items():
            with open(os.path.join(mig, name), "w") as f:
                f.write(sql)
        db.MIGRATIONS = mig


class MigrationTest(TempDbTest):
    def test_real_migrations_apply_once(self):
        self.assertTrue(db.migrate())
        self.assertEqual(db.migrate(), [])
        with db.read() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"schema_migrations", "counters", "settings", "scheduled_jobs"} <= tables)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_edited_migration_is_refused(self):
        self.use_migrations({"0001_a.sql": "CREATE TABLE a(x);"})
        db.migrate()
        self.use_migrations({"0001_a.sql": "CREATE TABLE a(x, y);"})
        with self.assertRaisesRegex(db.MigrationError, "changed after it was applied"):
            db.migrate()

    def test_failed_migration_leaves_nothing_behind(self):
        self.use_migrations({"0001_ok.sql": "CREATE TABLE a(x);",
                             "0002_bad.sql": "CREATE TABLE b(x);\nINSERT INTO nope VALUES (1);"})
        with self.assertRaises(db.MigrationError):
            db.migrate()
        with db.read() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            versions = [r[0] for r in c.execute("SELECT version FROM schema_migrations")]
        self.assertIn("a", tables)
        self.assertNotIn("b", tables)
        self.assertEqual(versions, [1])

    def test_database_newer_than_code_is_refused(self):
        self.use_migrations({"0001_a.sql": "CREATE TABLE a(x);", "0002_b.sql": "CREATE TABLE b(x);"})
        db.migrate()
        os.remove(os.path.join(db.MIGRATIONS, "0002_b.sql"))
        with self.assertRaisesRegex(db.MigrationError, "doesn't know"):
            db.migrate()

    def test_statement_splitting(self):
        sql = """-- comment line
CREATE TABLE t(x TEXT);
INSERT INTO t VALUES ('semi;colon');
CREATE TRIGGER t_ro BEFORE UPDATE ON t BEGIN SELECT RAISE(ABORT, 'no; updates'); END;
"""
        parts = db._statements(sql)
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[2].startswith("CREATE TRIGGER") and parts[2].endswith("END;"))
        with self.assertRaises(db.MigrationError):
            db._statements("CREATE TABLE x(")


class TransactionTest(TempDbTest):
    def setUp(self):
        super().setUp()
        db.migrate()

    def test_rollback_on_error(self):
        with self.assertRaises(ZeroDivisionError):
            with db.tx() as c:
                c.execute("INSERT INTO counters VALUES ('x', 1)")
                1 / 0
        with db.read() as c:
            self.assertIsNone(c.execute("SELECT * FROM counters").fetchone())

    def test_concurrent_writers_never_lose_updates(self):
        with db.tx() as c:
            c.execute("INSERT INTO counters VALUES ('n', 0)")

        def bump():
            with db.tx() as c:
                n = c.execute("SELECT value FROM counters WHERE name='n'").fetchone()[0]
                time.sleep(0.002)  # widen the race window
                c.execute("UPDATE counters SET value=? WHERE name='n'", (n + 1,))

        threads = [threading.Thread(target=bump) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT value FROM counters WHERE name='n'").fetchone()[0], 25)

    def test_settings_round_trip(self):
        with db.tx() as c:
            db.set_setting("hold_minutes", 35, c)
            db.set_setting("hold_minutes", 40, c)
        self.assertEqual(db.get_setting("hold_minutes"), 40)
        self.assertEqual(db.get_setting("missing", "dflt"), "dflt")

    def test_foreign_keys_are_enforced(self):
        with db.tx() as c:
            c.execute("CREATE TABLE p(id INTEGER PRIMARY KEY)")
            c.execute("CREATE TABLE ch(p INTEGER REFERENCES p(id))")
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO ch VALUES (99)")


class HealthTest(ServerTestCase):
    def test_healthz(self):
        r = self.client().get("/healthz")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertTrue(os.path.exists(config.DB_PATH))
