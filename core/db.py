"""
PostgreSQL connection pool + schema management.

Usage:
    from core.db import get_pool

    pool = get_pool()           # singleton, loads config/config.yaml
    pool.init_schema()          # ensure tables + indexes exist

    with pool.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.pool
import psycopg2.errors
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS snapshots (
    scan_id          INTEGER,
    scan_time        TEXT,
    match_id         TEXT,
    match_key        TEXT,
    league           TEXT,
    home             TEXT,
    away             TEXT,
    status_text      TEXT,
    phase            INTEGER,
    home_score       INTEGER,
    away_score       INTEGER,
    ou_line          TEXT,
    over_odds        TEXT,
    under_odds       TEXT,
    ou_line_open     TEXT,
    over_odds_open   TEXT,
    under_odds_open  TEXT,
    hdp_line         TEXT,
    hdp_home_odds    TEXT,
    hdp_away_odds    TEXT,
    hdp_line_open    TEXT,
    hdp_home_odds_open TEXT,
    hdp_away_odds_open TEXT,
    odds_source      TEXT DEFAULT 'nowscore',
    minute           INTEGER
);
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id               SERIAL PRIMARY KEY,
    signal_time      TEXT,
    match_id         TEXT,
    match_key        TEXT,
    league           TEXT,
    home             TEXT,
    away             TEXT,
    signal_type      TEXT,
    description      TEXT,
    trigger_minute   TEXT,
    trigger_score    TEXT,
    trigger_ou_line  TEXT,
    trigger_ou_line_open TEXT,
    trigger_over_odds TEXT,
    trigger_under_odds TEXT,
    odds_source      TEXT DEFAULT 'nowscore',
    final_score      TEXT,
    final_total_goals INTEGER,
    signal_result    TEXT,
    updated_at       TEXT
);
"""

_CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    match_id          TEXT,
    match_key         TEXT UNIQUE,
    match_date        TEXT,
    league            TEXT,
    home              TEXT,
    away              TEXT,
    final_home_score  INTEGER,
    final_away_score  INTEGER,
    total_goals       INTEGER,
    first_ou_line     TEXT,
    first_over_odds   TEXT,
    first_under_odds  TEXT,
    first_hdp_line    TEXT,
    last_ou_line      TEXT,
    last_over_odds    TEXT,
    last_under_odds   TEXT,
    last_hdp_line     TEXT,
    is_finished       INTEGER DEFAULT 0,
    updated_at        TEXT
);
"""

_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id        SERIAL PRIMARY KEY,
    scan_time      TEXT,
    total_matches  INTEGER,
    live_matches   INTEGER,
    source         TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_snap_dedup ON snapshots (match_key, scan_time, odds_source)",
]

# Columns that may be missing in older databases – idempotent migration.
_ENSURE_COLUMNS = [
    ("snapshots", "odds_source", "TEXT DEFAULT 'nowscore'"),
    ("snapshots", "minute",      "INTEGER"),
    ("signals",   "odds_source", "TEXT DEFAULT 'nowscore'"),
    ("signals",   "final_score", "TEXT"),
    ("signals",   "final_total_goals", "INTEGER"),
    ("signals",   "signal_result",     "TEXT"),
    ("signals",   "updated_at",        "TEXT"),
]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "config.yaml",
)


def _load_config(config_path: str) -> dict:
    """Load YAML config and return the full dict."""
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _build_dsn(cfg: dict) -> str:
    """Build a libpq DSN string from the 'database' section of config."""
    db = cfg.get("database", cfg)  # allow flat or nested
    host = db.get("host", "127.0.0.1")
    port = db.get("port", 5432)
    name = db.get("name", db.get("dbname", "betting"))
    user = db.get("user", "postgres")
    password = db.get("password", "")
    return f"host={host} port={port} dbname={name} user={user} password={password}"


# ---------------------------------------------------------------------------
# DatabasePool
# ---------------------------------------------------------------------------

class DatabasePool:
    """Thread-safe PostgreSQL connection pool with schema management."""

    def __init__(self, config_path: Optional[str] = None, min_conn: int = 1, max_conn: int = 5):
        config_path = config_path or _DEFAULT_CONFIG_PATH
        self._cfg = _load_config(config_path)

        # Allow pool size override from config
        pool_cfg = self._cfg.get("pool", {})
        self._min = pool_cfg.get("min", min_conn)
        self._max = pool_cfg.get("max", max_conn)

        dsn = _build_dsn(self._cfg)
        logger.info("Connecting to PostgreSQL … (pool %d–%d)", self._min, self._max)
        self._pool = psycopg2.pool.ThreadedConnectionPool(self._min, self._max, dsn)
        logger.info("Connection pool ready.")

    # -- connection context manager ------------------------------------------

    @contextmanager
    def get_conn(self):
        """Yield a connection; auto-return to pool on exit.

        Usage::

            with pool.get_conn() as conn:
                cur = conn.cursor()
                ...
        """
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass

    # -- schema management ---------------------------------------------------

    def init_schema(self):
        """Ensure all tables, columns, and indexes exist (idempotent)."""
        with self.get_conn() as conn:
            cur = conn.cursor()

            # 1. Create tables
            for ddl in (_CREATE_SNAPSHOTS, _CREATE_SIGNALS, _CREATE_RESULTS, _CREATE_SCANS):
                cur.execute(ddl)
            conn.commit()

            # 2. Ensure columns (for legacy databases)
            for tbl, col, typ in _ENSURE_COLUMNS:
                try:
                    cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
                    conn.commit()
                except psycopg2.errors.DuplicateColumn:
                    conn.rollback()

            # 3. Create indexes
            for idx_sql in _CREATE_INDEXES:
                try:
                    cur.execute(idx_sql)
                    conn.commit()
                except Exception as exc:
                    logger.warning("Index creation note: %s", exc)
                    conn.rollback()

            cur.close()
        logger.info("Schema initialised.")

    # -- lifecycle -----------------------------------------------------------

    def close(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()
            logger.info("Connection pool closed.")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_pool: Optional[DatabasePool] = None


def get_pool(config_path: Optional[str] = None) -> DatabasePool:
    """Return (or create) the module-level singleton DatabasePool."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = DatabasePool(config_path=config_path)
    return _pool
