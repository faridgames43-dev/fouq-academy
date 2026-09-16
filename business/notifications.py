"""Notification Center - in-app today, architecture ready for WhatsApp/SMS/
Email later (see NotificationChannel stub) but we never claim those work
without a real API connection."""
from datetime import date
from db import q, q1, ex
from business.entitlements import get_compensation_expiring_soon
from business.subscriptions import sync_subscription_statuses


def create_notification(conn, type_, title, body=None, user_id=None, player_id=None, dedupe=True):
    if dedupe:
        existing = q1(
            conn,
            """SELECT id FROM notifications WHERE type=? AND date(created_at)=date('now')
               AND COALESCE(player_id,-1)=COALESCE(?,-1) AND COALESCE(user_id,-1)=COALESCE(?,-1)""",
            (type_, player_id, user_id),
        )
        if existing:
            return existing["id"]
    return ex(
        conn,
        "INSERT INTO notifications(user_id, player_id, type, title, body) VALUES (?,?,?,?,?)",
        (user_id, player_id, type_, title, body),
    )


def run_daily_checks(conn, admin_user_ids=None):
    """Idempotent (dedup by day) sweep that raises the alerts described in
    the spec. Safe to call on every admin dashboard load."""
    admin_user_ids = admin_user_ids or []
    sync_subscription_statuses(conn)
    created = 0

    expiring = q(
        conn,
        """SELECT s.*, p.first_name, p.last_name, p.id as pid FROM subscriptions s
           JOIN players p ON p.id = s.player_id WHERE s.status='EXPIRING_SOON'""",
    )
    for s in expiring:
        create_notification(conn, "SUBSCRIPTION_EXPIRING",
                             f"اشتراك {s['first_name']} {s['last_name']} سينتهي قريبًا",
                             f"ينتهي في {s['end_date']}", player_id=s["pid"])
        created += 1

    expired = q(
        conn,
        """SELECT s.*, p.first_name, p.last_name, p.id as pid FROM subscriptions s
           JOIN players p ON p.id = s.player_id WHERE s.status='EXPIRED'
           AND s.id = (SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id = s.player_id)""",
    )
    for s in expired:
        create_notification(conn, "SUBSCRIPTION_EXPIRED",
                             f"انتهى اشتراك {s['first_name']} {s['last_name']}",
                             "يحتاج تجديد أو مراجعة الحصص المستحقة", player_id=s["pid"])
        created += 1

    comp_expiring = get_compensation_expiring_soon(conn, days_ahead=3)
    for c in comp_expiring:
        create_notification(conn, "COMPENSATION_EXPIRING",
                             f"حصص مستحقة لـ {c['first_name']} {c['last_name']} ستنتهي صلاحيتها",
                             f"تنتهي في {c['expires_at']}", player_id=c["player_id"])
        created += 1

    return created


def unread_count(conn, user_id):
    row = q1(conn, "SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0", (user_id,))
    return row["c"] if row else 0


def list_for_user(conn, user_id, limit=30):
    return q(conn, "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))


def list_recent(conn, limit=30):
    return q(conn, "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))


def mark_read(conn, notification_id):
    ex(conn, "UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
