"""SQLite schema and helpers for routercut collector."""

from __future__ import annotations

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
        """
    )
    conn.commit()


@contextmanager
def get_connection(db_path: Path | str | None = None):
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()
