"""
Session Entitlements Engine
============================
Implements the core business rule of FOUQ Academy:

    "انتهاء الاشتراك لا يعني إسقاط حق اللاعب في الحصص المستحقة"
    Subscription expiry does NOT cancel a player's right to earned/owed sessions.

Every credit a player can use to attend a training session is stored as an
"entitlement batch" (like an inventory lot): REGULAR (from a subscription),
COMPENSATION (owed sessions with their own expiry policy), BONUS (admin
grants), or LEGACY (leftover credit from the old Tawasol club).

Consumption follows FEFO (First-Expiring-First-Out): batches with an
explicit expiry date (compensation/bonus/legacy) are consumed before
open-ended REGULAR batches, nearest expiry first.

Nothing here ever allows a negative remaining quantity, a double deduction
for the same (player, training_session), or silent data loss: every
mutation is mirrored in session_ledger (business/audit trail).
"""
from datetime import date, datetime
from db import q, q1, ex
from business.audit import log as audit_log

REASON_LABELS = {
    "CANCELLED_SESSION": "إلغاء حصة من الأكاديمية",
    "APPROVED_EXCUSE": "عذر معتمد",
    "ADMIN_DECISION": "قرار إداري",
    "EXCEPTIONAL": "تعويض استثنائي",
    "OTHER": "سبب آخر",
}

ATTENDANCE_DEDUCTING_STATUSES = ("PRESENT", "LATE")


class EntitlementError(Exception):
    pass


def today_str():
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Expiration maintenance (lazy, run before any read that matters)
# ---------------------------------------------------------------------------

def sync_expirations(conn, player_id=None, user_id=None):
    """Move any ACTIVE batch whose expires_at has passed into EXPIRED,
    zero its remaining quantity and record the loss in the ledger so the
    number is always explainable."""
    params = []
    sql = """SELECT * FROM session_entitlements
             WHERE status='ACTIVE' AND expires_at IS NOT NULL AND expires_at < ?"""
    params.append(today_str())
    if player_id:
        sql += " AND player_id=?"
        params.append(player_id)
    expired = q(conn, sql, tuple(params))
    for batch in expired:
        remaining = batch["quantity_remaining"]
        ex(conn, "UPDATE session_entitlements SET status='EXPIRED', quantity_remaining=0 WHERE id=?", (batch["id"],))
        if remaining > 0:
            ex(
                conn,
                """INSERT INTO session_ledger(player_id, entitlement_id, action, entitlement_type, quantity, reason, user_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (batch["player_id"], batch["id"], "EXPIRE", batch["type"], -remaining,
                 "انتهت صلاحية الحصص التعويضية/الإضافية", user_id),
            )
    return len(expired)


# ---------------------------------------------------------------------------
# Granting
# ---------------------------------------------------------------------------

def grant_entitlement(conn, player_id, etype, quantity, subscription_id=None, expires_at=None,
                       reason_code=None, reason_text=None, user_id=None):
    if quantity <= 0:
        raise EntitlementError("الكمية يجب أن تكون أكبر من صفر")
    batch_id = ex(
        conn,
        """INSERT INTO session_entitlements
           (player_id, subscription_id, type, reason_code, reason_text, quantity_total,
            quantity_remaining, expires_at, status, created_by)
           VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?)""",
        (player_id, subscription_id, etype, reason_code, reason_text, quantity, quantity, expires_at, user_id),
    )
    ex(
        conn,
        """INSERT INTO session_ledger(player_id, entitlement_id, action, entitlement_type, quantity, reason, user_id, subscription_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (player_id, batch_id, "GRANT", etype, quantity, reason_text or REASON_LABELS.get(reason_code, "منح حصص"),
         user_id, subscription_id),
    )
    audit_log(conn, user_id, "GRANT_ENTITLEMENT", "session_entitlements", batch_id,
              after={"player_id": player_id, "type": etype, "quantity": quantity, "expires_at": expires_at},
              reason=reason_text)
    return batch_id


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------

def get_balances(conn, player_id):
    """Return dict with per-type remaining totals + grand total, always
    filtering out expired/used/cancelled/reversed batches."""
    rows = q(
        conn,
        """SELECT type, COALESCE(SUM(quantity_remaining),0) as qty
           FROM session_entitlements
           WHERE player_id=? AND status='ACTIVE'
           GROUP BY type""",
        (player_id,),
    )
    balances = {"REGULAR": 0, "COMPENSATION": 0, "BONUS": 0, "LEGACY": 0}
    for r in rows:
        balances[r["type"]] = r["qty"]
    balances["TOTAL"] = sum(balances.values())
    return balances


def get_eligible_batches(conn, player_id):
    """FEFO ordered list of usable batches (expiry-bearing batches first,
    nearest expiry first; open-ended REGULAR last, oldest first)."""
    return q(
        conn,
        """SELECT * FROM session_entitlements
           WHERE player_id=? AND status='ACTIVE' AND quantity_remaining > 0
           ORDER BY (expires_at IS NULL) ASC, expires_at ASC, created_at ASC""",
        (player_id,),
    )


def get_compensation_expiring_soon(conn, days_ahead=3):
    cutoff = date.today().toordinal() + days_ahead
    rows = q(
        conn,
        """SELECT se.*, p.first_name, p.last_name, p.player_code
           FROM session_entitlements se JOIN players p ON p.id = se.player_id
           WHERE se.status='ACTIVE' AND se.type IN ('COMPENSATION','BONUS')
             AND se.expires_at IS NOT NULL AND se.quantity_remaining > 0""",
    )
    out = []
    for r in rows:
        try:
            exp_ord = date.fromisoformat(r["expires_at"]).toordinal()
        except Exception:
            continue
        if exp_ord <= cutoff:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Consumption (attendance deduction) & reversal
# ---------------------------------------------------------------------------

def consume_one_session(conn, player_id, training_session_id=None, user_id=None, allow_override=False):
    """Deduct exactly one session using FEFO. Returns (entitlement_id, entitlement_type)
    or (None, 'ADMIN_OVERRIDE') if allow_override is used because no batch is eligible.
    Raises EntitlementError if nothing is eligible and no override is permitted."""
    sync_expirations(conn, player_id, user_id)
    batches = get_eligible_batches(conn, player_id)
    if not batches:
        if allow_override:
            audit_log(conn, user_id, "ADMIN_OVERRIDE_ATTENDANCE", "players", player_id,
                      reason="تسجيل حضور بدون حصة متاحة عبر صلاحية إدارية")
            return None, "ADMIN_OVERRIDE"
        raise EntitlementError("لا توجد حصص متاحة لهذا اللاعب (يحتاج تجديد)")

    batch = batches[0]
    new_remaining = batch["quantity_remaining"] - 1
    new_status = "USED" if new_remaining <= 0 else "ACTIVE"
    ex(conn, "UPDATE session_entitlements SET quantity_remaining=?, status=? WHERE id=?",
       (new_remaining, new_status, batch["id"]))
    ledger_id = ex(
        conn,
        """INSERT INTO session_ledger(player_id, entitlement_id, action, entitlement_type, quantity, reason, user_id, training_session_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (player_id, batch["id"], "ATTENDANCE_DEDUCT", batch["type"], -1, "خصم حصة حضور", user_id, training_session_id),
    )
    return batch["id"], batch["type"], ledger_id


def reverse_consumption(conn, entitlement_id, ledger_id=None, user_id=None, attendance_id=None):
    """Reverse exactly one deducted session back into the SAME entitlement
    batch it came from. If that batch has since expired, the credit is
    restored for record-keeping but the batch stays non-eligible (policy),
    which the caller can override administratively if needed."""
    if entitlement_id is None:
        return  # nothing was deducted (e.g. was ABSENT) -> nothing to reverse
    batch = q1(conn, "SELECT * FROM session_entitlements WHERE id=?", (entitlement_id,))
    if not batch:
        return
    new_remaining = batch["quantity_remaining"] + 1
    # Only flip back to ACTIVE if the batch isn't itself expired/cancelled
    new_status = batch["status"]
    if batch["status"] == "USED":
        new_status = "ACTIVE"
    # EXPIRED/CANCELLED batches keep their status: credit is restored for
    # bookkeeping accuracy but remains ineligible for consumption (per policy).
    ex(conn, "UPDATE session_entitlements SET quantity_remaining=?, status=? WHERE id=?",
       (new_remaining, new_status, entitlement_id))
    ex(
        conn,
        """INSERT INTO session_ledger(player_id, entitlement_id, action, entitlement_type, quantity, reason, user_id, attendance_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (batch["player_id"], entitlement_id, "ATTENDANCE_REVERSE", batch["type"], 1, "عكس خصم حضور (إلغاء تحضير)",
         user_id, attendance_id),
    )


def administrative_adjustment(conn, player_id, etype, quantity_delta, reason, user_id):
    """Manual add/remove of sessions by an authorized admin. Never allows
    the resulting total for that type to go negative."""
    balances = get_balances(conn, player_id)
    if quantity_delta < 0 and balances.get(etype, 0) + quantity_delta < 0:
        raise EntitlementError("لا يمكن أن يصبح رصيد الحصص سالبًا")
    if quantity_delta > 0:
        grant_entitlement(conn, player_id, etype, quantity_delta, reason_code="ADMIN_DECISION",
                           reason_text=reason, user_id=user_id)
    else:
        remaining_to_remove = -quantity_delta
        batches = get_eligible_batches(conn, player_id)
        for b in batches:
            if b["type"] != etype or remaining_to_remove <= 0:
                continue
            take = min(b["quantity_remaining"], remaining_to_remove)
            new_remaining = b["quantity_remaining"] - take
            new_status = "USED" if new_remaining <= 0 else "ACTIVE"
            ex(conn, "UPDATE session_entitlements SET quantity_remaining=?, status=? WHERE id=?",
               (new_remaining, new_status, b["id"]))
            ex(
                conn,
                """INSERT INTO session_ledger(player_id, entitlement_id, action, entitlement_type, quantity, reason, user_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (player_id, b["id"], "ADMIN_ADJUST", etype, -take, reason, user_id),
            )
            remaining_to_remove -= take
    audit_log(conn, user_id, "ADMIN_ADJUST_ENTITLEMENT", "players", player_id,
              after={"type": etype, "delta": quantity_delta}, reason=reason)
