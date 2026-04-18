"""Gestión de leads.db SQLite — leads, emails enviados, tracking."""
import sqlite3
import json
from datetime import datetime

DB_PATH = "/var/www/neuralops/leads.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            company TEXT,
            email TEXT UNIQUE,
            website TEXT,
            sector TEXT,
            project_slug TEXT,
            score INTEGER DEFAULT 0,
            source TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS emails_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            project_slug TEXT,
            subject TEXT,
            body TEXT,
            sent_at TEXT DEFAULT (datetime('now')),
            tracking_id TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT,
            event TEXT,
            ip TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    return conn


def save_lead(name: str, company: str, email: str, sector: str,
              project_slug: str, source: str, website: str = "") -> bool:
    """Returns True if new lead, False if already exists."""
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO leads (name,company,email,website,sector,project_slug,source) VALUES (?,?,?,?,?,?,?)",
                (name, company, email, website, sector, project_slug, source),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_leads(status: str = None, min_score: int = 0, limit: int = 50) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM leads WHERE status=? AND score>=? ORDER BY score DESC LIMIT ?",
                (status, min_score, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leads WHERE score>=? ORDER BY score DESC LIMIT ?",
                (min_score, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def update_lead(email: str, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    with _conn() as conn:
        conn.execute(f"UPDATE leads SET {sets} WHERE email=?", (*kwargs.values(), email))


def save_email_sent(lead_id: int, project_slug: str, subject: str, body: str, tracking_id: str):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO emails_sent (lead_id,project_slug,subject,body,tracking_id) VALUES (?,?,?,?,?)",
            (lead_id, project_slug, subject, body, tracking_id),
        )
