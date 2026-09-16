import json
from db import q1, ex

DEFAULTS = {
    "compensation_expiry_days": "30",
    "consumption_priority": "COMPENSATION_FEFO_THEN_REGULAR",
    "freeze_policy_days": "7",
    "assessment_weights": json.dumps({"SKILL": 40, "FITNESS": 25, "BEHAVIOR": 20, "DISCIPLINE": 15}),
    "renewal_alert_days": json.dumps([7, 3, 1]),
    "sessions_alert_thresholds": json.dumps([5, 3, 1, 0]),
    "coach_daily_points_cap": "20",
    "coach_daily_compensation_cap": "0",  # coaches cannot grant compensation at all by default
    "academy_name": "أكاديمية فوق",
    "academy_tagline": "رايحين فوق!",
}


def get_setting(conn, key, default=None):
    row = q1(conn, "SELECT value FROM settings WHERE key=?", (key,))
    if row:
        return row["value"]
    return DEFAULTS.get(key, default)


def get_setting_json(conn, key, default=None):
    raw = get_setting(conn, key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def set_setting(conn, key, value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    existing = q1(conn, "SELECT key FROM settings WHERE key=?", (key,))
    if existing:
        ex(conn, "UPDATE settings SET value=? WHERE key=?", (value, key))
    else:
        ex(conn, "INSERT INTO settings(key, value) VALUES (?,?)", (key, value))


def ensure_defaults(conn):
    for k, v in DEFAULTS.items():
        existing = q1(conn, "SELECT key FROM settings WHERE key=?", (k,))
        if not existing:
            ex(conn, "INSERT INTO settings(key, value) VALUES (?,?)", (k, v))
