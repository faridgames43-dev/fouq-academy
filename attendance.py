"""Attendance marking. One row per (training_session, player) - status
changes update that row in place so a player can never be double-deducted
for the same session, and every deduction/reversal is mirrored 1:1 in the
session ledger.
"""
from db import q, q1, ex
from business.entitlements import (
    consume_one_session, reverse_consumption, EntitlementError,
)
from business.subscriptions import get_attendance_eligibility
from business.audit import log as audit_log

DEDUCTING = ("PRESENT", "LATE")


class AttendanceError(Exception):
    pass


def get_attendance_for_session(conn, training_session_id):
    return q(
        conn,
        """SELECT a.*, p.first_name, p.last_name, p.photo_url, p.player_code
           FROM attendance a JOIN players p ON p.id = a.player_id
           WHERE a.training_session_id=?""",
        (training_session_id,),
    )


def get_existing(conn, training_session_id, player_id):
    return q1(conn, "SELECT * FROM attendance WHERE training_session_id=? AND player_id=?",
              (training_session_id, player_id))


def mark_attendance(conn, training_session_id, player_id, new_status, user_id, allow_override=False):
    """Create or update the attendance row for (session, player).
    Returns dict: {already: bool, status, remaining_total, message}"""
    existing = get_existing(conn, training_session_id, player_id)

    if existing and existing["status"] == new_status and not existing["cancelled"]:
        from business.entitlements import get_balances
        bal = get_balances(conn, player_id)
        return {"already": True, "status": new_status, "remaining_total": bal["TOTAL"],
                "message": "تم تسجيل حضور اللاعب مسبقًا."}

    if not existing:
        entitlement_id = None
        ledger_id = None
        etype = None
        if new_status in DEDUCTING:
            eligibility = get_attendance_eligibility(conn, player_id)
            if eligibility["code"] == "NO_SESSIONS" and not allow_override:
                raise AttendanceError("لا يمكن تسجيل الحضور: " + eligibility["detail"])
            override = allow_override or eligibility["code"] == "ADMIN_OVERRIDE"
            entitlement_id, etype, ledger_id = consume_one_session(
                conn, player_id, training_session_id, user_id, allow_override=override
            )
        att_id = ex(
            conn,
            """INSERT INTO attendance(training_session_id, player_id, status, checked_by, entitlement_id, ledger_id)
               VALUES (?,?,?,?,?,?)""",
            (training_session_id, player_id, new_status, user_id, entitlement_id, ledger_id),
        )
        audit_log(conn, user_id, "MARK_ATTENDANCE", "attendance", att_id,
                  after={"status": new_status, "player_id": player_id})
    else:
        old_status = existing["status"]
        old_deducts = old_status in DEDUCTING
        new_deducts = new_status in DEDUCTING
        entitlement_id = existing["entitlement_id"]

        if old_deducts and not new_deducts:
            reverse_consumption(conn, entitlement_id, existing["ledger_id"], user_id, existing["id"])
            ex(conn, "UPDATE attendance SET status=?, entitlement_id=NULL, ledger_id=NULL, checked_by=?, cancelled=0 WHERE id=?",
               (new_status, user_id, existing["id"]))
        elif not old_deducts and new_deducts:
            eligibility = get_attendance_eligibility(conn, player_id)
            override = allow_override or eligibility["code"] == "ADMIN_OVERRIDE"
            if eligibility["code"] == "NO_SESSIONS" and not override:
                raise AttendanceError("لا يمكن تسجيل الحضور: " + eligibility["detail"])
            new_ent, new_etype, new_ledger = consume_one_session(
                conn, player_id, training_session_id, user_id, allow_override=override
            )
            ex(conn, "UPDATE attendance SET status=?, entitlement_id=?, ledger_id=?, checked_by=?, cancelled=0 WHERE id=?",
               (new_status, new_ent, new_ledger, user_id, existing["id"]))
        else:
            ex(conn, "UPDATE attendance SET status=?, checked_by=?, cancelled=0 WHERE id=?",
               (new_status, user_id, existing["id"]))
        audit_log(conn, user_id, "UPDATE_ATTENDANCE", "attendance", existing["id"],
                  before={"status": old_status}, after={"status": new_status})

    from business.entitlements import get_balances
    bal = get_balances(conn, player_id)
    return {"already": False, "status": new_status, "remaining_total": bal["TOTAL"],
            "message": "تم تسجيل الحضور بنجاح."}


def cancel_attendance(conn, attendance_id, user_id, reason="إلغاء تحضير"):
    """Reverse any consumption tied to this row, audit-log the full
    before-state, then remove the pointer row so the slot is free again."""
    row = q1(conn, "SELECT * FROM attendance WHERE id=?", (attendance_id,))
    if not row:
        raise AttendanceError("سجل الحضور غير موجود")
    if row["status"] in DEDUCTING:
        reverse_consumption(conn, row["entitlement_id"], row["ledger_id"], user_id, attendance_id)
    audit_log(conn, user_id, "CANCEL_ATTENDANCE", "attendance", attendance_id, before=row, reason=reason)
    ex(conn, "DELETE FROM attendance WHERE id=?", (attendance_id,))


def mark_all_present(conn, training_session_id, player_ids, user_id):
    results = []
    for pid in player_ids:
        try:
            r = mark_attendance(conn, training_session_id, pid, "PRESENT", user_id)
            results.append({"player_id": pid, "ok": True, **r})
        except AttendanceError as e:
            results.append({"player_id": pid, "ok": False, "message": str(e)})
    return results


def checkin_by_code(conn, training_session_id, player_code, user_id):
    """Barcode / QR / manual-code scan entrypoint used by the attendance screen."""
    player = q1(conn, "SELECT * FROM players WHERE player_code=?", (player_code.strip(),))
    if not player:
        raise AttendanceError("لم يتم العثور على لاعب بهذا الكود")
    session = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (training_session_id,))
    if not session:
        raise AttendanceError("الحصة غير موجودة")
    if player["group_id"] != session["group_id"]:
        # still allow but flag - coach may be covering another group
        pass
    result = mark_attendance(conn, training_session_id, player["id"], "PRESENT", user_id)
    result["player"] = player
    return result
