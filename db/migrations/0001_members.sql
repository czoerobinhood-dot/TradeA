PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_iterations INTEGER NOT NULL CHECK (password_iterations >= 100000),
    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS users_single_admin
ON users(role) WHERE role = 'admin';

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS shared_stocks (
    code TEXT PRIMARY KEY CHECK (length(code) = 6),
    name TEXT NOT NULL,
    first_added_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code TEXT NOT NULL REFERENCES shared_stocks(code) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, stock_code)
);

CREATE INDEX IF NOT EXISTS member_favorites_stock_code
ON member_favorites(stock_code);

CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code TEXT NOT NULL REFERENCES shared_stocks(code) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    pattern_label TEXT NOT NULL DEFAULT 'watch'
        CHECK (pattern_label IN ('watch', 'positive', 'negative')),
    timeframe TEXT NOT NULL DEFAULT 'daily'
        CHECK (timeframe IN ('daily', 'weekly')),
    sample_date TEXT NOT NULL,
    confidence INTEGER NOT NULL DEFAULT 3 CHECK (confidence BETWEEN 1 AND 5),
    nominated INTEGER NOT NULL DEFAULT 0 CHECK (nominated IN (0, 1)),
    review_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (review_status IN ('not_requested', 'pending', 'approved', 'rejected')),
    reviewed_by TEXT REFERENCES users(id),
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, stock_code)
);

CREATE INDEX IF NOT EXISTS annotations_stock_code ON annotations(stock_code);
CREATE INDEX IF NOT EXISTS annotations_review_status ON annotations(review_status);

CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    attempts INTEGER NOT NULL,
    window_started INTEGER NOT NULL
);
