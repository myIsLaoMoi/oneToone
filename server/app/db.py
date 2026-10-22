import sqlite3
import threading
from contextlib import contextmanager

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer (
    id             TEXT PRIMARY KEY,
    code           TEXT NOT NULL,
    token          TEXT NOT NULL UNIQUE,
    sender_key     TEXT NOT NULL,
    receiver_key   TEXT,
    transport      TEXT NOT NULL DEFAULT 'pending',
    status         TEXT NOT NULL DEFAULT 'waiting',
    created_at     REAL NOT NULL,
    code_expires_at REAL NOT NULL,
    expires_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transfer_code ON transfer(code);
CREATE INDEX IF NOT EXISTS idx_transfer_expires ON transfer(expires_at);

CREATE TABLE IF NOT EXISTS file (
    transfer_id TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mime        TEXT NOT NULL DEFAULT '',
    received    INTEGER NOT NULL DEFAULT 0,
    sha256      TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (transfer_id, idx)
);
"""


def init() -> None:
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    _conn = conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


@contextmanager
def tx():
    """Serialized write transaction."""
    if _conn is None:
        raise RuntimeError("database not initialized")
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    if _conn is None:
        raise RuntimeError("database not initialized")
    with _lock:
        return _conn.execute(sql, params).fetchall()


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None
