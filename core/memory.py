"""Lightweight SQLite-based key-value memory. ChromaDB added later."""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = "/var/www/neuralops/state.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            collection TEXT NOT NULL,
            id TEXT NOT NULL,
            document TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (collection, id)
        )
    """)
    conn.commit()
    return conn


def upsert(collection: str, id: str, document: str, metadata: dict = None):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO memory (collection, id, document, metadata) VALUES (?,?,?,?)",
            (collection, id, document, json.dumps(metadata or {})),
        )


def query(collection: str, where: dict = None, n_results: int = 10) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, document, metadata FROM memory WHERE collection=? ORDER BY created_at DESC LIMIT ?",
            (collection, n_results),
        ).fetchall()

    results = []
    for row in rows:
        meta = json.loads(row[2] or "{}")
        if where:
            if not all(meta.get(k) == v for k, v in where.items()):
                continue
        results.append({"id": row[0], "document": row[1], "metadata": meta})
    return results


def log_event(agent: str, event: str, data: dict = None):
    upsert(
        collection="events",
        id=f"{agent}_{datetime.now().isoformat()}",
        document=event,
        metadata={"agent": agent, "timestamp": datetime.now().isoformat(), **(data or {})},
    )
