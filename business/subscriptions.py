"""Subscription lifecycle: Subscription Period is deliberately decoupled
from Session Entitlement (see business/entitlements.py). A subscription's
own status only describes the *time period* the player paid for; whether
they may still attend is answered separately by get_attendance_eligibility().
"""
from datetime import date, timedelta
from db import q, q1, ex
from business.entitlements import grant_entitlement, get_balances, sync_expirations
from business.audit import log as audit_log
from business.settings_lib import get_setting


def today():
    return date.today()


def sync_subscription_statuses(conn, player_id=None):
    """Recompute time-based status for subscriptions that aren't in a
    manually-set terminal/administrative state (FROZEN/CANCELLED)."""
    sql = "SELECT * FROM subscriptions WHERE status NOT IN ('FROZEN','CANCELLED')"
    params = ()
    if player_id:
        sql += " AND player_id=?"
        params = (player_id,)
    subs = q(conn, sql, params)
    t = today()
    for s in subs:
        try:
            end = date.fromisoformat(s["end_date"])
        except Exception:
            continue
        if t > end:
            new_status = "EXPIRED"
        elif (end - t).days <= 7:
            new_status = "EXPIRING_SOON"
        else:
            new_status = "ACTIVE"
        if new_status != s["status"]:
            ex(conn, "UPDATE subscriptions SET status=? WHERE id=?", (new_status, s["id"]))


def create_subscription(conn, player_id, package, start_date, price, discount=0, paid_amount=0,
                         payment_method=None, invoice_ref=None, note=None, user_id=None,
                         sessions_count_override=None, duration_days_override=None):
    """Create a brand-new Subscription record (renewals NEVER edit an old
    one - full history is preserved) and grant its REGULAR entitlement batch."""
    duration_days = duration_days_override or package["duration_days"]
    sessions_count = sessions_count_override or package["sessions_count"]
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = start + timedelta(days=duration_days)
    sub_id = ex(
        conn,
        """INSERT INTO subscriptions(player_id, package_id, package_name_snapshot, start_date, end_date,
              price, discount, paid_amount, payment_method, payment_status, invoice_ref, status, note, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            player_id, package["id"], package["name"], start.isoformat(), end.isoformat(),
            price, discount, paid_amount, payment_method,
            _payment_status(price, discount, paid_amount), invoice_ref, "ACTIVE", note, user_id,
        ),
    )
    grant_entitlement(conn, player_id, "REGULAR", sessions_count, subscription_id=sub_id,
                       expires_at=None, reason_text=f"اشتراك: {package['name']}", user_id=user_id)
    audit_log(conn, user_id, "CREATE_SUBSCRIPTION", "subscriptions", sub_id,
              after={"player_id": player_id, "package": package["name"], "sessions": sessions_count},
              reason=note)
    return sub_id


def _payment_status(price, discount, paid):
    net = max(price - discount, 0)
    if paid <= 0:
        return "UNPAID"
    if paid >= net:
        return "PAID"
    return "PARTIALLY_PAID"


def get_latest_subscription(conn, player_id):
    return q1(conn, "SELECT * FROM subscriptions WHERE player_id=? ORDER BY start_date DESC, id DESC LIMIT 1",
              (player_id,))


def get_attendance_eligibility(conn, player_id):
    """The headline rule of the whole platform lives here.
    Returns one of: ELIGIBLE, COMPENSATION_ONLY, NO_SESSIONS, SUSPENDED, ADMIN_OVERRIDE
    plus a human explanation string."""
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if player and player["attendance_override"]:
        return {
            "code": "ADMIN_OVERRIDE",
            "label": "صلاحية إدارية للحضور",
            "detail": player["attendance_override_note"] or "سمحت الإدارة بالحضور استثنائيًا",
        }

    sync_expirations(conn, player_id)
    sync_subscription_statuses(conn, player_id)
    sub = get_latest_subscription(conn, player_id)
    balances = get_balances(conn, player_id)
    total = balances["TOTAL"]

    if sub and sub["status"] == "FROZEN":
        return {"code": "SUSPENDED", "label": "الاشتراك مجمّد", "detail": "الاشتراك مجمّد مؤقتًا حسب السياسة."}

    if sub and sub["status"] in ("ACTIVE", "EXPIRING_SOON"):
        if total > 0:
            return {"code": "ELIGIBLE", "label": "مؤهل للحضور",
                    "detail": f"الاشتراك نشط، ويتوفر {total} حصة قابلة للاستخدام."}
        return {"code": "NO_SESSIONS", "label": "نفدت الحصص",
                "detail": "الاشتراك نشط زمنيًا لكن لا توجد حصص متبقية — راجع الإدارة."}

    # subscription missing / expired / cancelled / renewal_pending
    if total > 0:
        comp = balances["COMPENSATION"] + balances["BONUS"] + balances["LEGACY"]
        return {
            "code": "COMPENSATION_ONLY",
            "label": "استكمال حصص مستحقة",
            "detail": f"انتهى الاشتراك، ويتبقى له {comp} حصة مستحقة يمكن استخدامها دون تجديد فوري.",
        }
    return {"code": "NO_SESSIONS", "label": "يحتاج تجديد",
            "detail": "لا يوجد اشتراك نشط ولا حصص مستحقة متبقية."}


def renew_subscription(conn, player_id, package, start_date, price, discount=0, paid_amount=0,
                        payment_method=None, invoice_ref=None, note=None, user_id=None):
    """Renewing NEVER touches existing compensation/bonus batches - they
    stay separate and keep their own expiry, exactly as required."""
    return create_subscription(conn, player_id, package, start_date, price, discount, paid_amount,
                                payment_method, invoice_ref, note, user_id)


def freeze_subscription(conn, subscription_id, days, reason, user_id):
    sub = q1(conn, "SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not sub:
        return
    from datetime import date as d
    frozen_until = (d.today() + timedelta(days=days)).isoformat()
    ex(conn, "UPDATE subscriptions SET status='FROZEN', frozen_from=?, frozen_until=? WHERE id=?",
       (d.today().isoformat(), frozen_until, subscription_id))
    audit_log(conn, user_id, "FREEZE_SUBSCRIPTION", "subscriptions", subscription_id,
              after={"frozen_until": frozen_until}, reason=reason)


def unfreeze_subscription(conn, subscription_id, user_id):
    ex(conn, "UPDATE subscriptions SET status='ACTIVE', frozen_from=NULL, frozen_until=NULL WHERE id=?",
       (subscription_id,))
    audit_log(conn, user_id, "UNFREEZE_SUBSCRIPTION", "subscriptions", subscription_id)


def cancel_subscription(conn, subscription_id, reason, user_id):
    ex(conn, "UPDATE subscriptions SET status='CANCELLED', note=? WHERE id=?", (reason, subscription_id))
    audit_log(conn, user_id, "CANCEL_SUBSCRIPTION", "subscriptions", subscription_id, reason=reason)
