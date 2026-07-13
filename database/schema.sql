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
