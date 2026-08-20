-- MyShare Database Schema

-- Users table
CREATE TABLE IF NOT EXISTS Users (
    ID INTEGER PRIMARY KEY,
    Username TEXT NOT NULL UNIQUE,
    Password TEXT NOT NULL,
    Email TEXT NOT NULL UNIQUE,
    FirstName TEXT NOT NULL,
    LastName TEXT NOT NULL
);

-- Holdings table
CREATE TABLE IF NOT EXISTS Holdings (
    ShareID INTEGER PRIMARY KEY,
    LotID INTEGER NOT NULL,
    User INTEGER NOT NULL,
    Symbol TEXT NOT NULL,
    BuyPrice REAL NOT NULL,
    BuyDate TEXT NOT NULL,
    SellLotID INTEGER,
    SellPrice REAL,
    SellDate TEXT,
    FOREIGN KEY (User) REFERENCES Users(ID)
);

-- PasswordReset table
CREATE TABLE IF NOT EXISTS PasswordReset (
    User INTEGER PRIMARY KEY,
    ResetCode TEXT NOT NULL,
    Created TEXT DEFAULT (datetime('now', 'localtime')),
    Attempts INTEGER DEFAULT 0,
    FOREIGN KEY (User) REFERENCES Users(ID)
);

-- Trigger to remove password reset codes older than 5 minutes
CREATE TRIGGER IF NOT EXISTS CleanOldPasswordResets
AFTER INSERT ON PasswordReset
BEGIN
    DELETE FROM PasswordReset WHERE (Cast((JulianDay('now', 'localtime') - JulianDay(Created, 'localtime')) * 24 * 60 AS INTEGER)) > 5;
END;

-- Indexes for Users table
CREATE INDEX IF NOT EXISTS idx_users_username ON Users(Username);
CREATE INDEX IF NOT EXISTS idx_users_email ON Users(Email);
CREATE INDEX IF NOT EXISTS idx_users_id ON Users(ID);

-- Indexes for Holdings table
CREATE INDEX IF NOT EXISTS idx_holdings_user ON Holdings(User);
CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON Holdings(Symbol);
CREATE INDEX IF NOT EXISTS idx_holdings_lotid ON Holdings(LotID);
CREATE INDEX IF NOT EXISTS idx_holdings_selllotid ON Holdings(SellLotID);
CREATE INDEX IF NOT EXISTS idx_holdings_shareid ON Holdings(ShareID);
CREATE INDEX IF NOT EXISTS idx_holdings_user_symbol ON Holdings(User, Symbol);
CREATE INDEX IF NOT EXISTS idx_holdings_user_selllotid ON Holdings(User, SellLotID);

-- ============================================================
-- Quant Desk tables (Kronos forecast -> skfolio allocation ->
-- NautilusTrader paper-fill simulation). See QUANT_DESK.md and
-- quant/db.py — quant/db.py's init_quant_schema() creates these
-- same tables at runtime via executescript(), so this file and
-- quant/db.py's SCHEMA string must be kept in sync; this copy
-- exists so the full DB shape is visible in one place alongside
-- the MyShare tables above.
-- ============================================================

-- Per-ticker forecast snapshots (one row per ticker per cycle run)
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

-- Target vs. current allocation weights per cycle run
CREATE TABLE IF NOT EXISTS quant_allocation (
    AsOf TEXT NOT NULL,
    Ticker TEXT NOT NULL,
    TargetWeight REAL NOT NULL,
    CurrentWeight REAL NOT NULL,
    Benchmark TEXT NOT NULL,
    PRIMARY KEY (AsOf, Ticker)
);

-- Paper (simulated, no real money) account equity/cash snapshots
CREATE TABLE IF NOT EXISTS quant_paper_account (
    AsOf TEXT PRIMARY KEY,
    Equity REAL NOT NULL,
    Cash REAL NOT NULL,
    StartingEquity REAL NOT NULL
);

-- Paper position snapshots, one row per ticker per cycle run
CREATE TABLE IF NOT EXISTS quant_paper_positions (
    AsOf TEXT NOT NULL,
    Ticker TEXT NOT NULL,
    Qty REAL NOT NULL,
    AvgPrice REAL NOT NULL,
    UnrealizedPnl REAL NOT NULL,
    PRIMARY KEY (AsOf, Ticker)
);

-- Forecast-accuracy scoreboard: each forecast is scored against its
-- realized outcome once HorizonHours has elapsed (see
-- quant/run_quant_cycle.py::score_due_forecasts). Powers the public
-- "was Kronos actually right?" scoreboard described in QUANT_DESK.md.
CREATE TABLE IF NOT EXISTS quant_forecast_outcomes (
    Ticker TEXT NOT NULL,
    GeneratedAt TEXT NOT NULL,
    HorizonHours INTEGER NOT NULL,
    PredictedDirection TEXT NOT NULL,
    PredictedReturnPct REAL NOT NULL,
    ActualReturnPct REAL,
    DirectionCorrect INTEGER,
    ScoredAt TEXT,
    PRIMARY KEY (Ticker, GeneratedAt)
);

-- Indexes for Quant Desk tables
CREATE INDEX IF NOT EXISTS idx_quant_forecasts_ticker ON quant_forecasts(Ticker);
CREATE INDEX IF NOT EXISTS idx_quant_allocation_asof ON quant_allocation(AsOf);
CREATE INDEX IF NOT EXISTS idx_quant_paper_positions_asof ON quant_paper_positions(AsOf);
CREATE INDEX IF NOT EXISTS idx_quant_outcomes_scored ON quant_forecast_outcomes(ScoredAt);
