"""Short per-user conversation memory in SQLite (last N turns)."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from config import HANDOFF_MUTE_HOURS, MEMORY_DB, MEMORY_LIMIT

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    lang TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

                CREATE TABLE IF NOT EXISTS processed (
                    message_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT PRIMARY KEY,
                    lang TEXT,
                    handoff_at REAL,
                    handoff_count INTEGER DEFAULT 0,
                    name TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def already_processed(message_id: str) -> bool:
    if not message_id:
        return False
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def mark_processed(message_id: str, user_id: str = "") -> None:
    if not message_id:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO processed(message_id, user_id, created_at) VALUES (?,?,?)",
                (message_id, user_id, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def append_message(user_id: str, role: str, content: str, lang: str | None = None) -> None:
    if not user_id or not content:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO messages(user_id, role, content, lang, created_at) VALUES (?,?,?,?,?)",
                (user_id, role, content, lang, time.time()),
            )
            # Keep only the last MEMORY_LIMIT rows for this user
            conn.execute(
                """
                DELETE FROM messages WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, MEMORY_LIMIT),
            )
            if lang:
                conn.execute(
                    """
                    INSERT INTO sessions(user_id, lang) VALUES (?,?)
                    ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang
                    """,
                    (user_id, lang),
                )
            conn.commit()
        finally:
            conn.close()


def get_history(user_id: str, limit: int = MEMORY_LIMIT) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT role, content, lang, created_at
                FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()


def set_name(user_id: str, name: str) -> None:
    if not user_id or not name:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO sessions(user_id, name) VALUES (?,?)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name
                """,
                (user_id, name),
            )
            conn.commit()
        finally:
            conn.close()


def record_handoff(user_id: str) -> int:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO sessions(user_id, handoff_at, handoff_count) VALUES (?,?,1)
                ON CONFLICT(user_id) DO UPDATE SET
                    handoff_at=excluded.handoff_at,
                    handoff_count=COALESCE(sessions.handoff_count, 0) + 1
                """,
                (user_id, time.time()),
            )
            row = conn.execute(
                "SELECT handoff_count FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
            conn.commit()
            return int(row["handoff_count"] if row else 1)
        finally:
            conn.close()


def handoff_count(user_id: str) -> int:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT handoff_count FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["handoff_count"] or 0) if row else 0
        finally:
            conn.close()


def is_handoff_muted(user_id: str) -> bool:
    """After a handoff, suppress further auto-replies for HANDOFF_MUTE_HOURS."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT handoff_at, handoff_count FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row or not row["handoff_at"]:
                return False
            age = time.time() - float(row["handoff_at"])
            return age < HANDOFF_MUTE_HOURS * 3600
        finally:
            conn.close()
