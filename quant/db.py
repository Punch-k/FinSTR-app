"""
SQLite helpers for the Quant Desk subsystem.

Mirrors the parameterized-query convention already used in app.py's
executeDatabaseQuery/executeDatabaseUpdate (never string-format user or
model-derived values into SQL — always pass them as params), but lives in
its own module rather than importing from app.py, since app.py imports the
quant blueprint (importing back would be circular).

Uses the same physical database file as MyShare (SQLITE_DATABASE below must
stay in sync with app.py's SQLITE_DATABASE) so nothing needs a new datastore.
"""

import sqlite3
from contextlib import contextmanager

# Deliberately NOT database/MyShare.db: that file is gitignored because it
# holds real user accounts/holdings, and CI needs to commit the quant state
# for GitHub Pages / the local Flask app to read it back. quant.db only ever
# holds public, model-derived data (forecasts, allocations, the paper
# account) — never user data — so it's safe to check into git. Same SQLite
# approach as MyShare, just a separate file for a separate trust boundary.
SQLITE_DATABASE = "database/quant.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_forecasts (
    Ticker TEXT NOT NULL,
    GeneratedAt TEXT NOT NULL,
    DirectionConfidence REAL NOT NULL,
    ExpectedReturnPct REAL NOT NULL,
    IntervalLowPct REAL NOT NULL,
    IntervalHighPct REAL NOT NULL,
    VolatilityForecast REAL NOT NULL,
    ModelName TEXT NOT NULL,
    PRIMARY KEY (Ticker, GeneratedAt)
);

CREATE TABLE IF NOT EXISTS quant_allocation (
    AsOf TEXT NOT NULL,
    Ticker TEXT NOT NULL,
    TargetWeight REAL NOT NULL,
    CurrentWeight REAL NOT NULL,
    Benchmark TEXT NOT NULL,
    Method TEXT NOT NULL DEFAULT 'confidence-weighted-tilt',
    PRIMARY KEY (AsOf, Ticker)
);

CREATE TABLE IF NOT EXISTS quant_paper_account (
    AsOf TEXT PRIMARY KEY,
    Equity REAL NOT NULL,
    Cash REAL NOT NULL,
    StartingEquity REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quant_paper_positions (
    AsOf TEXT NOT NULL,
    Ticker TEXT NOT NULL,
    Qty REAL NOT NULL,
    AvgPrice REAL NOT NULL,
    UnrealizedPnl REAL NOT NULL,
    PRIMARY KEY (AsOf, Ticker)
);

CREATE TABLE IF NOT EXISTS quant_forecast_outcomes (
    Ticker TEXT NOT NULL,
    GeneratedAt TEXT NOT NULL,
    HorizonHours INTEGER NOT NULL,
    PredictedDirection TEXT NOT NULL,
    PredictedReturnPct REAL NOT NULL,
    PriceAtForecast REAL NOT NULL,
    ActualReturnPct REAL,
    DirectionCorrect INTEGER,
    ScoredAt TEXT,
    PRIMARY KEY (Ticker, GeneratedAt)
);

CREATE INDEX IF NOT EXISTS idx_quant_forecasts_ticker ON quant_forecasts(Ticker);
CREATE INDEX IF NOT EXISTS idx_quant_allocation_asof ON quant_allocation(AsOf);
CREATE INDEX IF NOT EXISTS idx_quant_paper_positions_asof ON quant_paper_positions(AsOf);
CREATE INDEX IF NOT EXISTS idx_quant_outcomes_scored ON quant_forecast_outcomes(ScoredAt);
"""


def _resolve_db_path(db_path):
    # Resolved lazily at call time rather than via a `db_path=SQLITE_DATABASE`
    # signature default: a mutable module-global used as a signature default
    # is captured once at import time and silently ignores later
    # reassignment of SQLITE_DATABASE (e.g. by tests pointing at a temp DB).
    return SQLITE_DATABASE if db_path is None else db_path


@contextmanager
def get_connection(db_path=None):
    connection = sqlite3.connect(_resolve_db_path(db_path))
    connection.execute("PRAGMA foreign_keys = ON;")
    try:
        yield connection
    finally:
        connection.close()


# Columns added to an existing table after its first release. CREATE TABLE IF NOT
# EXISTS above is a no-op against an already-existing table, so a column added here
# needs an explicit migration or every insert against a pre-existing quant.db (e.g.
# one already committed by a prior CI run) breaks with "no column named X".
_COLUMN_MIGRATIONS = {
    "quant_forecast_outcomes": [
        ("PriceAtForecast", "REAL NOT NULL DEFAULT 0"),
    ],
    "quant_allocation": [
        ("Method", "TEXT NOT NULL DEFAULT 'confidence-weighted-tilt'"),
    ],
}


def _apply_column_migrations(connection):
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table});")}
        for name, coltype in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype};")


def init_quant_schema(db_path=None):
    """Idempotent — safe to call on every run, like init_screener_cache() in app.py."""
    with get_connection(db_path) as connection:
        connection.executescript(SCHEMA)
        _apply_column_migrations(connection)
        connection.commit()


def execute_query(query, params=(), db_path=None):
    with get_connection(db_path) as connection:
        cursor = connection.cursor()
        try:
            return cursor.execute(query, params).fetchall()
        except sqlite3.Error:
            return []


def execute_update(statement, params=(), db_path=None):
    with get_connection(db_path) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(statement, params)
            connection.commit()
            return cursor.rowcount
        except sqlite3.Error:
            connection.rollback()
            return 0


def executemany_update(statement, param_list, db_path=None):
    with get_connection(db_path) as connection:
        cursor = connection.cursor()
        try:
            cursor.executemany(statement, param_list)
            connection.commit()
            return cursor.rowcount
        except sqlite3.Error:
            connection.rollback()
            return 0
