"""SQLite schema and helpers for routercut collector."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "routercut.db"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            name TEXT,
            local_root TEXT NOT NULL,
            result_subdir TEXT NOT NULL DEFAULT 'Result2',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hosts_ip ON hosts(ip);
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id INTEGER NOT NULL REFERENCES hosts(id) ON DELETE CASCADE,
            folder_date TEXT NOT NULL,
            model TEXT,
            time TEXT NOT NULL,
            barcode TEXT NOT NULL,
            cam INTEGER NOT NULL,
            roi INTEGER NOT NULL,
            result TEXT NOT NULL,
            value TEXT,
            spec TEXT,
            image_file TEXT,
            source_csv TEXT,
            UNIQUE(host_id, folder_date, time, barcode, cam, roi)
        );
        CREATE INDEX IF NOT EXISTS idx_rec_host_date ON records(host_id, folder_date);
        CREATE INDEX IF NOT EXISTS idx_rec_time_barcode ON records(host_id, time, barcode);
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hosts)").fetchall()}
    if "smb_share" not in cols:
        conn.execute("ALTER TABLE hosts ADD COLUMN smb_share TEXT")
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    cur = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    if row is None or row[0] is None:
        return default
    return str(row[0])


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_smb_credentials(conn: sqlite3.Connection) -> tuple[str, str, str]:
    """SMB (user, password, domain). DB 설정 우선, 비어 있으면 환경 변수."""
    u = (get_setting(conn, "smb_user", "") or "").strip()
    if not u:
        u = (os.environ.get("ROUTERCUT_SMB_USER", "") or "").strip()
    p = get_setting(conn, "smb_password", "")
    if not p:
        p = os.environ.get("ROUTERCUT_SMB_PASSWORD", "") or ""
    d = (get_setting(conn, "smb_domain", "") or "").strip()
    if not d:
        d = (os.environ.get("ROUTERCUT_SMB_DOMAIN", "") or "").strip()
    return u, p, d


@contextmanager
def get_connection(db_path: Path | str | None = None):
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()
