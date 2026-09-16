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
