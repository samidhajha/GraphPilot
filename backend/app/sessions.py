"""Stores finished investigations in SQLite (separate from the supply-chain graph, which stays read-only)."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graphpilot.db")


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with closing(_conn()) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS investigations ("
            "investigation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, question TEXT NOT NULL, "
            "status TEXT NOT NULL, risk_level TEXT, body TEXT NOT NULL)")


def save(response):
    init()
    with closing(_conn()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO investigations VALUES (?, ?, ?, ?, ?, ?)",
            (response["investigation_id"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
             response["question"], response["status"], response.get("risk_level"), json.dumps(response)))


def get(investigation_id):
    init()
    with closing(_conn()) as conn:
        row = conn.execute("SELECT body FROM investigations WHERE investigation_id = ?", (investigation_id,)).fetchone()
    return json.loads(row["body"]) if row else None


def list_recent(limit=20):
    init()
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT investigation_id, question, status, risk_level, created_at FROM investigations "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]