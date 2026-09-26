"""The booking database (SQLite, stdlib).

Usage:
    with db.tx() as c:            # one write transaction (BEGIN IMMEDIATE)
        c.execute("INSERT ...")
    with db.read() as c:          # reads outside a transaction
        rows = c.execute("SELECT ...").fetchall()

Each call opens its own short-lived connection and always closes it, so
threads never share one. Writers take SQLite's write lock up front
(BEGIN IMMEDIATE), which is what makes capacity checks safe: two parents
can't both see "1 place left" and both book it.

Never make network calls (Stripe, email, SMS) inside tx(): commit first,
then call out, then record the result in a second transaction.

Schema changes live in hah/migrations/NNNN_name.sql and are applied in
order at start-up. They're forward-only; an applied file must never be
edited (its checksum is recorded and start-up refuses a mismatch)."""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time

from . import config

MIGRATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
MIN_SQLITE = (3, 31, 0)
_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


def now():
    """UTC instant as stored in *_at columns: 2026-09-26T19:04:52Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _open(path=None):
    c = sqlite3.connect(path or config.DB_PATH, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 5000")
    c.execute("PRAGMA synchronous = FULL")  # money and children's records: durability first
    c.execute("PRAGMA temp_store = MEMORY")
    return c


@contextlib.contextmanager
def read():
    c = _open()
    try:
        yield c
    finally:
        c.close()


@contextlib.contextmanager
def tx():
    """A write transaction: commits on success, rolls back on any error."""
    c = _open()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")
    finally:
        c.close()


# ---------------------------------------------------------------- migrations


def migration_files():
    """[(version, name, sql, checksum)] in order, from hah/migrations."""
    out = []
    for fn in sorted(os.listdir(MIGRATIONS)):
        m = _MIGRATION_RE.match(fn)
        if not m:
            continue
        with open(os.path.join(MIGRATIONS, fn), encoding="utf-8") as f:
            sql = f.read()
        out.append((int(m.group(1)), m.group(2), sql, hashlib.sha256(sql.encode()).hexdigest()))
    versions = [v for v, *_ in out]
    if len(versions) != len(set(versions)):
        raise MigrationError("two migrations share a version number")
    return out


def migrate():
    """Create or upgrade the database. Returns the versions applied now."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise MigrationError("SQLite %s is too old (need %s+)"
                             % (sqlite3.sqlite_version, ".".join(map(str, MIN_SQLITE))))
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    c = _open()
    try:
        c.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
        c.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
                       version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                       checksum TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        done = {r["version"]: r for r in c.execute("SELECT * FROM schema_migrations")}
        applied = []
        for version, name, sql, checksum in migration_files():
            if version in done:
                if done[version]["checksum"] != checksum:
                    raise MigrationError(
                        "migration %04d_%s.sql was changed after it was applied; "
                        "add a new migration instead" % (version, name))
                continue
            try:
                c.execute("BEGIN IMMEDIATE")
                for statement in _statements(sql):
                    c.execute(statement)
                c.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                          (version, name, checksum, now()))
                c.execute("COMMIT")
            except Exception as e:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise MigrationError("migration %04d_%s failed: %s" % (version, name, e)) from e
            applied.append(version)
        unknown = set(done) - {v for v, *_ in migration_files()}
        if unknown:
            raise MigrationError("database has migrations this code doesn't know: %s"
                                 % sorted(unknown))
        return applied
    finally:
        c.close()


def _statements(sql):
    """Split a migration into statements (so all of it runs in one transaction).

    Uses sqlite3.complete_statement, so semicolons inside strings and
    CREATE TRIGGER ... BEGIN ... END; bodies are handled correctly."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        if not buf and line.strip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip().strip(";").strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise MigrationError("unterminated SQL statement: %s" % buf.strip()[:80])
    return out


# ---------------------------------------------------------------- settings


def get_setting(key, default=None, c=None):
    with (contextlib.nullcontext(c) if c else read()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key, value, c):
    """Inside a tx(): settings(key) = value (stored as JSON)."""
    c.execute("INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (key, json.dumps(value), now()))


def health():
    """True if the database answers a trivial query."""
    try:
        with read() as c:
            return c.execute("SELECT 1").fetchone()[0] == 1
    except sqlite3.Error:
        return False
