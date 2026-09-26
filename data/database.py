"""
database.py — SQLite persistence for the Research Agent.

Uses thread-local connections to prevent the ResourceWarning
flood caused by unclosed sqlite3 connections under async load.
"""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "research.db"

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Return the thread-local connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def _execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_connection()
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS findings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            source_url  TEXT,
            title       TEXT,
            summary     TEXT NOT NULL,
            relevance   TEXT,
            saved_at    TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL UNIQUE,
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
    """)
    conn.commit()


# ── Session operations ─────────────────────────────────────────────────────────

def create_session(session_id: str, question: str) -> None:
    _execute(
        "INSERT OR IGNORE INTO sessions (id, question, created_at, status) VALUES (?, ?, ?, ?)",
        (session_id, question, datetime.now().isoformat(), "active"),
    )


def get_session(session_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def list_sessions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, question, created_at, status FROM sessions ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Finding operations ─────────────────────────────────────────────────────────

def save_finding(
    session_id: str,
    summary: str,
    source_url: str = "",
    title: str = "",
    relevance: str = "",
) -> int:
    cursor = _execute(
        """INSERT INTO findings
           (session_id, source_url, title, summary, relevance, saved_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, source_url, title, summary, relevance, datetime.now().isoformat()),
    )
    return cursor.lastrowid


def list_findings(session_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, source_url, title, summary, relevance, saved_at
           FROM findings WHERE session_id = ?
           ORDER BY saved_at ASC""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_findings(session_id: str) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM findings WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row["c"]


# ── Report operations ──────────────────────────────────────────────────────────

def save_report(session_id: str, content: str) -> None:
    _execute(
        """INSERT OR REPLACE INTO reports (session_id, content, created_at)
           VALUES (?, ?, ?)""",
        (session_id, content, datetime.now().isoformat()),
    )
    _execute(
        "UPDATE sessions SET status = 'completed' WHERE id = ?",
        (session_id,),
    )


def get_report(session_id: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT content FROM reports WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row["content"] if row else None