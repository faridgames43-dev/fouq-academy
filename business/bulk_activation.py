"""One-off bulk correction: activate every player's subscription status in
one pass, following the exact rules the academy's admin specified:

  1. FOUQ players (new academy membership) always get their subscription
     activated (a fresh 30-day period), no matter their current session
     balance. If — and only if — their balance is currently empty (0), it
     is topped up to exactly 8 sessions. A player who already has *any*
     balance (more or less than 8) keeps it untouched.
  2. LEGACY players (carried over from Tawasol club) get their subscription
     activated the same way, UNLESS their balance is already depleted (0)
     — those are left as-is (no invented balance, no fake activation).
  3. A player whose latest subscription is already ACTIVE/EXPIRING_SOON is
     left alone (nothing to do). A player whose latest subscription is
     FROZEN or CANCELLED is left alone too — those are deliberate admin
     decisions this bulk pass must not silently override.
  4. "Activating" means creating a brand-new subscription row (full history
     is preserved, same spirit as every other renewal in this app) dated
     today, ending in 30 days, status ACTIVE, marked fully PAID at the
     player's best-matching active package price. Unlike a normal
     subscribe/renew flow, this does NOT also grant a fresh REGULAR
     session batch — only the balance top-up described in (1) touches a
     player's session count, and only when explicitly required.

Every subscription created and every balance top-up is fully audit-logged,
same as any other mutation in the system.

Because this touches every player's subscription/financial record in one
shot, callers MUST run compute_plan() first, show it to a human for review,
and only call execute_plan() with that same reviewed plan.
"""
from datetime import date, timedelta
from db import q, q1, ex
from business.entitlements import get_balances, administrative_adjustment
from business.subscriptions import get_latest_subscription
from business.audit import log as audit_log

TOPUP_TARGET = 8
DURATION_DAYS = 30
UNTOUCHED_STATUSES = ("ACTIVE", "EXPIRING_SOON", "FROZEN", "CANCELLED")


def _default_package(conn, player):
    """Best-matching active package for this player's branch/category —
    exact branch+category match preferred, falling back to a package with
    no branch/category restriction. Cheapest match wins when several tie,
    so the auto-generated financial record stays conservative."""
    return q1(conn, """
        SELECT * FROM packages
        WHERE active=1
          AND (category_id=? OR category_id IS NULL)
          AND (branch_id=? OR branch_id IS NULL)
        ORDER BY (category_id IS NULL) ASC, (branch_id IS NULL) ASC, price ASC
        LIMIT 1
    """, (player["category_id"], player["branch_id"]))


def compute_plan(conn):
    """Read-only: returns a list of one dict per player describing exactly
    what would happen, with no database writes. Always call this to build
    what the human reviews before execute_plan()."""
    players = q(conn, """SELECT p.*, c.name as category_name, b.name as branch_name
                          FROM players p LEFT JOIN categories c ON c.id=p.category_id
                          JOIN branches b ON b.id=p.branch_id ORDER BY p.id""")
    plan = []
    for p in players:
        balance = get_balances(conn, p["id"])["TOTAL"]
        latest_sub = get_latest_subscription(conn, p["id"])
        cur_status = latest_sub["status"] if latest_sub else None
        row = {
            "player_id": p["id"], "name": f"{p['first_name']} {p['last_name']}",
            "player_code": p["player_code"], "player_type": p["player_type"],
            "branch_name": p["branch_name"], "category_name": p["category_name"] or "بدون فئة",
            "current_balance": balance, "current_sub_status": cur_status or "لا يوجد",
        }
        if cur_status in UNTOUCHED_STATUSES:
            row["action"] = "skip"
            row["reason"] = "الاشتراك بالفعل ساري" if cur_status in ("ACTIVE", "EXPIRING_SOON") \
                else ("مجمّد — قرار إداري متعمّد" if cur_status == "FROZEN" else "ملغى — قرار إداري متعمّد")
            plan.append(row)
            continue

        is_fouq = p["player_type"] == "FOUQ"
        topup = TOPUP_TARGET if (is_fouq and balance == 0) else 0
        if not is_fouq and balance == 0:
            row["action"] = "skip"
            row["reason"] = "رصيده منتهٍ (لاعب سابق بنادي تواصل الرياضي)"
            plan.append(row)
            continue

        package = _default_package(conn, p)
        if not package:
            row["action"] = "skip"
            row["reason"] = "لا توجد باقة نشطة تطابق فرعه/فئته لتسجيلها كسعر الاشتراك"
            plan.append(row)
            continue

        row["action"] = "activate"
        row["topup"] = topup
        row["new_balance"] = balance + topup
        row["package_id"] = package["id"]
        row["package_name"] = package["name"]
        row["price"] = package["price"]
        plan.append(row)

    return plan


def execute_plan(conn, plan, admin_user_id):
    """Applies exactly the 'activate' rows of a plan produced by
    compute_plan(). Re-reading current state isn't done here on purpose —
    the caller re-runs compute_plan() right before this to avoid acting on
    a stale preview (see the route)."""
    today = date.today()
    end = today + timedelta(days=DURATION_DAYS)
    activated = 0
    topped_up = 0
    total_revenue = 0.0

    for row in plan:
        if row["action"] != "activate":
            continue
        pid = row["player_id"]
        sub_id = ex(
            conn,
            """INSERT INTO subscriptions(player_id, package_id, package_name_snapshot, start_date, end_date,
                  price, discount, paid_amount, payment_method, payment_status, invoice_ref, status, note, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, row["package_id"], row["package_name"], today.isoformat(), end.isoformat(),
             row["price"], 0, row["price"], "تفعيل جماعي إداري", "PAID", None, "ACTIVE",
             "تفعيل جماعي لحالة الاشتراك — لا يمنح حصصًا جديدة من الباقة", admin_user_id),
        )
        audit_log(conn, admin_user_id, "BULK_ACTIVATE_SUBSCRIPTION", "subscriptions", sub_id,
                  after={"player_id": pid, "package": row["package_name"], "price": row["price"],
                         "end_date": end.isoformat()},
                  reason="تفعيل جماعي لحالة الاشتراك لكل اللاعبين")
        activated += 1
        total_revenue += row["price"]

        if row.get("topup"):
            administrative_adjustment(conn, pid, "BONUS", row["topup"],
                                       "تفعيل جماعي - تعبئة رصيد فارغ إلى 8 حصص (أكاديمية فوق)", admin_user_id)
            topped_up += 1

    return {"activated": activated, "topped_up": topped_up, "total_revenue": total_revenue}
