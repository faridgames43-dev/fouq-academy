PRAGMA foreign_keys = ON;

-- =========================================================
-- FOUQ ACADEMY OPERATING SYSTEM - CORE SCHEMA
-- =========================================================

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT,
    address TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('SUPER_ADMIN','PROJECT_MANAGER','BRANCH_MANAGER','SUPERVISOR','COACH','PARENT','PLAYER')),
    branch_id INTEGER REFERENCES branches(id),
    active INTEGER NOT NULL DEFAULT 1,
    avatar_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS permissions_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    permission_key TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    min_age INTEGER,
    max_age INTEGER,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS facilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE REFERENCES users(id),
    name TEXT NOT NULL,
    phone TEXT,
    branch_id INTEGER REFERENCES branches(id),
    photo_url TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS groups_ (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    coach_id INTEGER REFERENCES coaches(id),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS parents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE REFERENCES users(id),
    name TEXT NOT NULL,
    phone TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_code TEXT UNIQUE NOT NULL,           -- used for Barcode / QR / lookup
    user_id INTEGER REFERENCES users(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    photo_url TEXT,
    dob TEXT,
    gender TEXT DEFAULT 'M',
    category_id INTEGER REFERENCES categories(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    group_id INTEGER REFERENCES groups_(id),
    coach_id INTEGER REFERENCES coaches(id),
    player_type TEXT NOT NULL DEFAULT 'FOUQ' CHECK(player_type IN ('FOUQ','LEGACY')),
    join_date TEXT NOT NULL DEFAULT (date('now')),
    referral_code TEXT UNIQUE,
    referred_by_player_id INTEGER REFERENCES players(id),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','INACTIVE','TRIAL','CONVERSION_PENDING')),
    onboarding_json TEXT,  -- checklist state
    notes TEXT,
    attendance_override INTEGER NOT NULL DEFAULT 0,
    attendance_override_note TEXT,
    attendance_override_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS parent_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL REFERENCES parents(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    relation TEXT DEFAULT 'ولي أمر',
    UNIQUE(parent_id, player_id)
);

-- =========================================================
-- PACKAGES / SUBSCRIPTIONS / ENTITLEMENTS / LEDGER
-- =========================================================

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    branch_id INTEGER REFERENCES branches(id),
    category_id INTEGER REFERENCES categories(id),
    price REAL NOT NULL,
    duration_days INTEGER NOT NULL DEFAULT 30,
    sessions_count INTEGER NOT NULL DEFAULT 12,
    days_per_week INTEGER NOT NULL DEFAULT 3,
    freeze_policy_days INTEGER NOT NULL DEFAULT 7,
    compensation_expiry_days INTEGER NOT NULL DEFAULT 30,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    package_id INTEGER REFERENCES packages(id),
    package_name_snapshot TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    discount REAL NOT NULL DEFAULT 0,
    paid_amount REAL NOT NULL DEFAULT 0,
    payment_method TEXT,
    payment_status TEXT NOT NULL DEFAULT 'UNPAID' CHECK(payment_status IN ('PAID','PARTIALLY_PAID','UNPAID','REFUNDED')),
    invoice_ref TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','EXPIRING_SOON','EXPIRED','FROZEN','CANCELLED','RENEWAL_PENDING')),
    frozen_from TEXT,
    frozen_until TEXT,
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Entitlement batches: inventory-lot style, supports FEFO consumption
CREATE TABLE IF NOT EXISTS session_entitlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    subscription_id INTEGER REFERENCES subscriptions(id),
    type TEXT NOT NULL CHECK(type IN ('REGULAR','COMPENSATION','BONUS','LEGACY')),
    reason_code TEXT,          -- for COMPENSATION: CANCELLED_SESSION, APPROVED_EXCUSE, ADMIN_DECISION, EXCEPTIONAL, OTHER
    reason_text TEXT,
    quantity_total INTEGER NOT NULL,
    quantity_remaining INTEGER NOT NULL,
    expires_at TEXT,           -- NULL = no independent expiry (regular tied to subscription end)
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','USED','EXPIRED','REVERSED','CANCELLED')),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    entitlement_id INTEGER REFERENCES session_entitlements(id),
    action TEXT NOT NULL CHECK(action IN ('GRANT','ATTENDANCE_DEDUCT','ATTENDANCE_REVERSE','EXPIRE','ADMIN_ADJUST','CANCEL')),
    entitlement_type TEXT,
    quantity INTEGER NOT NULL,     -- signed
    reason TEXT,
    user_id INTEGER REFERENCES users(id),
    training_session_id INTEGER,
    subscription_id INTEGER,
    attendance_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================
-- SCHEDULE / ATTENDANCE
-- =========================================================

CREATE TABLE IF NOT EXISTS training_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    coach_id INTEGER REFERENCES coaches(id),
    facility_id INTEGER REFERENCES facilities(id),
    session_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    goal TEXT,
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED','STARTED','COMPLETED','CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    training_session_id INTEGER NOT NULL REFERENCES training_sessions(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    status TEXT NOT NULL CHECK(status IN ('PRESENT','LATE','ABSENT','EXCUSED','FROZEN')),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    checked_by INTEGER REFERENCES users(id),
    entitlement_id INTEGER REFERENCES session_entitlements(id),
    ledger_id INTEGER REFERENCES session_ledger(id),
    cancelled INTEGER NOT NULL DEFAULT 0,
    cancelled_at TEXT,
    cancelled_by INTEGER REFERENCES users(id),
    UNIQUE(training_session_id, player_id)
);

-- =========================================================
-- ASSESSMENTS / DEVELOPMENT
-- =========================================================

CREATE TABLE IF NOT EXISTS assessment_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,   -- SKILL, FITNESS, BEHAVIOR, DISCIPLINE
    name TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 25
);

CREATE TABLE IF NOT EXISTS assessment_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES assessment_categories(id),
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    type TEXT NOT NULL CHECK(type IN ('INITIAL','PERIODIC','QUICK')),
    assessment_date TEXT NOT NULL DEFAULT (date('now')),
    coach_id INTEGER REFERENCES coaches(id),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assessment_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id),
    metric_id INTEGER NOT NULL REFERENCES assessment_metrics(id),
    score REAL NOT NULL CHECK(score >= 0 AND score <= 100)
);

-- =========================================================
-- LEVELS
-- =========================================================

CREATE TABLE IF NOT EXISTS levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_order INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    min_attendance_rate REAL DEFAULT 0,
    min_overall_score REAL DEFAULT 0,
    min_discipline_score REAL DEFAULT 0,
    description TEXT
);

CREATE TABLE IF NOT EXISTS player_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    level_id INTEGER NOT NULL REFERENCES levels(id),
    achieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by INTEGER REFERENCES users(id),
    current INTEGER NOT NULL DEFAULT 1
);

-- =========================================================
-- POINTS (رصيد فوق) / ACHIEVEMENTS / REWARDS
-- =========================================================

CREATE TABLE IF NOT EXISTS points_wallets (
    player_id INTEGER PRIMARY KEY REFERENCES players(id),
    balance INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS points_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    amount INTEGER NOT NULL,     -- signed
    reason TEXT NOT NULL,
    category TEXT NOT NULL,      -- ATTENDANCE, DEVELOPMENT, BEHAVIOR, CHALLENGE, ACHIEVEMENT, RENEWAL, REFERRAL, ADMIN, REDEMPTION
    user_id INTEGER REFERENCES users(id),
    balance_before INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    training_session_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT DEFAULT '🏆',
    points_reward INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS player_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    achievement_id INTEGER NOT NULL REFERENCES achievements(id),
    earned_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(player_id, achievement_id)
);

CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    photo_url TEXT,
    cost INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reward_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    reward_id INTEGER NOT NULL REFERENCES rewards(id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','READY','COMPLETED','CANCELLED')),
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    points_transaction_id INTEGER REFERENCES points_transactions(id)
);

-- =========================================================
-- REFERRALS / CRM / TRIALS
-- =========================================================

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_player_id INTEGER NOT NULL REFERENCES players(id),
    referred_name TEXT,
    referred_phone TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','QUALIFIED','REWARDED','CANCELLED')),
    resulting_player_id INTEGER REFERENCES players(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_name TEXT NOT NULL,
    child_name TEXT NOT NULL,
    child_age INTEGER,
    phone TEXT,
    source TEXT,
    campaign TEXT,
    owner_id INTEGER REFERENCES users(id),
    branch_id INTEGER REFERENCES branches(id),
    status TEXT NOT NULL DEFAULT 'NEW' CHECK(status IN ('NEW','CONTACTED','TRIAL_BOOKED','TRIAL_ATTENDED','NO_SHOW','INTERESTED','PAID','LOST','FOLLOW_UP')),
    last_contact TEXT,
    next_follow_up TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    trial_date TEXT NOT NULL,
    group_id INTEGER REFERENCES groups_(id),
    attended INTEGER,
    assessment_notes TEXT,
    recommendation TEXT,
    converted_player_id INTEGER REFERENCES players(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS renewal_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    contacted_by INTEGER REFERENCES users(id),
    note TEXT,
    stage TEXT NOT NULL DEFAULT 'CONTACTED',
    next_follow_up TEXT,
    outcome TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================
-- NOTIFICATIONS / AUDIT / SETTINGS
-- =========================================================

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    player_id INTEGER REFERENCES players(id),
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_players_branch ON players(branch_id);
CREATE INDEX IF NOT EXISTS idx_players_group ON players(group_id);
CREATE INDEX IF NOT EXISTS idx_subs_player ON subscriptions(player_id);
CREATE INDEX IF NOT EXISTS idx_entitlements_player ON session_entitlements(player_id);
CREATE INDEX IF NOT EXISTS idx_ledger_player ON session_ledger(player_id);
CREATE INDEX IF NOT EXISTS idx_attendance_player ON attendance(player_id);
CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(training_session_id);
CREATE INDEX IF NOT EXISTS idx_training_sessions_date ON training_sessions(session_date);
