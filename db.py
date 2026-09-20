import sqlite3
import os
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "academy.db")


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    """Transactional context manager. Commits on success, rolls back on error."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    conn = get_conn()
    with open(os.path.join(BASE_DIR, "schema.sql"), "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _column_names(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def migrate_db():
    """Additive-only, idempotent schema evolution. Runs on EVERY app start
    (not just first-run) so a live production database picks up new
    columns/tables safely, without ever dropping or rewriting existing data.
    Every statement here must be safe to run repeatedly."""
    conn = get_conn()
    cur = conn.cursor()

    def add_column(table, coldef):
        colname = coldef.split()[0]
        if colname not in _column_names(conn, table):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")

    # --- new tables (safe: CREATE TABLE IF NOT EXISTS) ---
    cur.execute("""CREATE TABLE IF NOT EXISTS challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        icon TEXT DEFAULT '🎯',
        points_reward INTEGER NOT NULL DEFAULT 15,
        target_type TEXT NOT NULL,
        target_value INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS player_challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL REFERENCES players(id),
        challenge_id INTEGER NOT NULL REFERENCES challenges(id),
        assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','COMPLETED','EXPIRED')),
        completed_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS subscription_alerts_sent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscription_id INTEGER NOT NULL,
        threshold TEXT NOT NULL,
        sent_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(subscription_id, threshold)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL REFERENCES groups_(id),
        title TEXT NOT NULL,
        description TEXT,
        due_date TEXT,
        points_reward INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER REFERENCES users(id),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS assignment_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL REFERENCES assignments(id),
        player_id INTEGER NOT NULL REFERENCES players(id),
        file_url TEXT NOT NULL,
        file_type TEXT,
        submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED')),
        reviewed_by INTEGER REFERENCES users(id),
        reviewed_at TEXT,
        feedback_note TEXT,
        UNIQUE(assignment_id, player_id)
    )""")
    # --- new columns on existing tables ---
    add_column("training_sessions", "player_of_session_id INTEGER REFERENCES players(id)")
    add_column("training_sessions", "session_note TEXT")
    add_column("points_transactions", "note TEXT")
    add_column("users", "must_reset_password INTEGER NOT NULL DEFAULT 0")

    conn.commit()

    # --- idempotent data seeding (new achievements / challenges) ---
    from business.achievements import ensure_extra_achievements
    ensure_extra_achievements(conn)
    from business.challenges import ensure_default_challenges
    ensure_default_challenges(conn)
    conn.commit()
    conn.close()


def q(conn, sql, params=()):
    """Run a SELECT, return list of dict rows."""
    cur = conn.execute(sql, params)
    return cur.fetchall()


def q1(conn, sql, params=()):
    """Run a SELECT, return one dict row or None."""
    cur = conn.execute(sql, params)
    return cur.fetchone()


def ex(conn, sql, params=()):
    """Run an INSERT/UPDATE/DELETE, return lastrowid."""
    cur = conn.execute(sql, params)
    return cur.lastrowid
