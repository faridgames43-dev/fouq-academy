"""شاشة التحضير TV: بدء/إغلاق التحضير من الشاشة نفسها.

القاعدة (قابلة للتعديل من الإعدادات، بتوقيت السعودية):
  • عند ضغط "بدء التحضير" تبدأ نافذة التحضير المبكر (افتراضيًا 20 دقيقة):
    كل لاعب يُمسح باركوده خلالها = حاضر + 40 نقطة تلقائيًا (الحضور المبكر).
  • بعد انتهاء النافذة يصبح أي لاعب يُمسح = متأخر + 20 نقطة تلقائيًا.
  • عند ضغط "إغلاق التحضير" يتوقف المسح، ومن لم يُحضَّر يُسجَّل غائبًا
    (الغياب لا يخصم أي حصة)، وتُغلق الحصة.
النقاط تُمنح مرة واحدة فقط لكل لاعب في الحصة، وتُعكس تلقائيًا إذا أُلغي
تحضيره أو غُيّرت حالته إلى غائب/بعذر.
"""
from datetime import datetime
from db import q, q1, ex
from business import attendance as att
from business.attendance import AttendanceError
from business.audit import log as audit_log
from business.points import (
    award_points, get_balance, attendance_points_awarded, EARLY_REASON, LATE_REASON,
)
from business.settings_lib import get_setting
from business.levels import current_level
from business import achievements as ach
from business import challenges as chal

FMT = "%Y-%m-%d %H:%M:%S"


def now_ksa():
    """الوقت الحالي بتوقيت السعودية (الإعداد TZ في db.py)."""
    return datetime.now()


def get_config(conn):
    def _int(key, default):
        try:
            return int(get_setting(conn, key, str(default)))
        except (TypeError, ValueError):
            return default
    return {
        "early_minutes": _int("checkin_early_minutes", 20),
        "early_points": _int("checkin_early_points", 40),
        "late_points": _int("checkin_late_points", 20),
    }


def get_phase(conn, ts):
    """ts: صف training_sessions. يرجع (phase, elapsed_seconds, early_remaining_seconds)
    حيث phase: NOT_STARTED | EARLY | LATE | CLOSED."""
    cfg = get_config(conn)
    started = ts.get("checkin_started_at")
    closed = ts.get("checkin_closed_at")
    if not started:
        return "NOT_STARTED", 0, cfg["early_minutes"] * 60
    try:
        elapsed = int((now_ksa() - datetime.strptime(started, FMT)).total_seconds())
    except Exception:
        elapsed = 0
    elapsed = max(elapsed, 0)
    early_total = cfg["early_minutes"] * 60
    if closed:
        return "CLOSED", elapsed, max(early_total - elapsed, 0)
    if elapsed < early_total:
        return "EARLY", elapsed, early_total - elapsed
    return "LATE", elapsed, 0


def _session(conn, session_id):
    return q1(conn, """SELECT ts.*, g.name AS group_name FROM training_sessions ts
                       JOIN groups_ g ON g.id = ts.group_id WHERE ts.id=?""", (session_id,))


def start_checkin(conn, session_id, user_id):
    ts = _session(conn, session_id)
    if not ts:
        raise AttendanceError("الحصة غير موجودة")
    if ts["checkin_started_at"] and not ts["checkin_closed_at"]:
        return ts  # بدأ مسبقًا — لا نعيد تصفير المؤقت
    ex(conn, "UPDATE training_sessions SET checkin_started_at=?, checkin_closed_at=NULL, status='STARTED' WHERE id=?",
       (now_ksa().strftime(FMT), session_id))
    audit_log(conn, user_id, "START_CHECKIN", "training_sessions", session_id)
    return _session(conn, session_id)


def close_checkin(conn, session_id, user_id, mark_absent=True):
    ts = _session(conn, session_id)
    if not ts:
        raise AttendanceError("الحصة غير موجودة")
    absent = 0
    if mark_absent:
        roster = q(conn, "SELECT id FROM players WHERE group_id=?", (ts["group_id"],))
        marked = {r["player_id"] for r in q(conn, "SELECT player_id FROM attendance WHERE training_session_id=?", (session_id,))}
        for p in roster:
            if p["id"] not in marked:
                att.mark_attendance(conn, session_id, p["id"], "ABSENT", user_id)
                absent += 1
    ex(conn, "UPDATE training_sessions SET checkin_closed_at=?, status='COMPLETED' WHERE id=?",
       (now_ksa().strftime(FMT), session_id))
    audit_log(conn, user_id, "CLOSE_CHECKIN", "training_sessions", session_id, after={"marked_absent": absent})
    return {"marked_absent": absent}


def checkin_player(conn, session_id, user_id, code=None, player_id=None):
    """يحضّر لاعبًا من شاشة TV حسب مرحلة الوقت ويمنحه نقاطه. يرجع dict جاهز للعرض."""
    ts = _session(conn, session_id)
    if not ts:
        raise AttendanceError("الحصة غير موجودة")
    phase, _, _ = get_phase(conn, ts)
    if phase == "NOT_STARTED":
        raise AttendanceError("لم يبدأ التحضير بعد — اضغط زر «بدء التحضير» الأخضر أولًا")
    if phase == "CLOSED":
        raise AttendanceError("تم إغلاق التحضير لهذه الحصة")

    if player_id:
        player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    else:
        player = q1(conn, "SELECT * FROM players WHERE UPPER(player_code)=UPPER(?)", ((code or "").strip(),))
    if not player:
        raise AttendanceError("لم يتم العثور على لاعب بهذا الكود")

    status = "PRESENT" if phase == "EARLY" else "LATE"
    existing = att.get_existing(conn, session_id, player["id"])
    already = bool(existing and existing["status"] in att.DEDUCTING)
    if already:
        # مُحضَّر مسبقًا: لا نغيّر حالته (مبكر يبقى مبكرًا) ولا نمنحه نقاطًا مرة ثانية
        from business.entitlements import get_balances
        status = existing["status"]
        result = {"remaining_total": get_balances(conn, player["id"])["TOTAL"],
                  "no_balance": bool(existing["no_balance"]), "message": "تم تسجيل حضور اللاعب مسبقًا."}
    else:
        result = att.mark_attendance(conn, session_id, player["id"], status, user_id)

    cfg = get_config(conn)
    points_awarded = 0
    reason = None
    if not already:
        if not attendance_points_awarded(conn, player["id"], session_id):
            points_awarded = cfg["early_points"] if status == "PRESENT" else cfg["late_points"]
            reason = EARLY_REASON if status == "PRESENT" else LATE_REASON
            if points_awarded:
                award_points(conn, player["id"], points_awarded, reason, "ATTENDANCE", user_id,
                             training_session_id=session_id)
        new_codes = ach.check_after_attendance(conn, player["id"], user_id)
        if points_awarded:
            new_codes += ach.check_after_points(conn, player["id"], user_id)
        chal.check_and_complete(conn, player["id"], user_id)
    else:
        new_codes = []

    new_achievements = []
    for c in new_codes:
        a = q1(conn, "SELECT name, icon FROM achievements WHERE code=?", (c,))
        if a:
            new_achievements.append({"name": a["name"], "icon": a["icon"]})

    lvl = current_level(conn, player["id"])
    return {
        "already": already,
        "status": status,
        "phase": phase,
        "player": _player_card(conn, player, {"status": status, "no_balance": result.get("no_balance")}),
        "points_awarded": points_awarded,
        "points_reason": reason,
        "points": get_balance(conn, player["id"]),
        "level_name": lvl["name"] if lvl else "الانطلاقة",
        "remaining_total": result["remaining_total"],
        "no_balance": result.get("no_balance", False),
        "new_achievements": new_achievements,
        "message": result["message"],
    }


def _player_card(conn, p, att_row=None):
    achs = q(conn, """SELECT a.icon, a.name FROM player_achievements pa
                      JOIN achievements a ON a.id = pa.achievement_id
                      WHERE pa.player_id=? ORDER BY pa.earned_at DESC, pa.id DESC""", (p["id"],))
    lvl = current_level(conn, p["id"])
    return {
        "id": p["id"],
        "name": f"{p['first_name']} {p['last_name']}",
        "first_name": p["first_name"],
        "code": p["player_code"],
        "photo_url": p.get("photo_url"),
        "celebration_url": p.get("celebration_url"),
        "points": get_balance(conn, p["id"]),
        "level": lvl["name"] if lvl else "الانطلاقة",
        "achievements": [{"icon": a["icon"], "name": a["name"]} for a in achs],
        "status": att_row["status"] if att_row else None,
        "no_balance": bool(att_row.get("no_balance")) if att_row else False,
    }


def roster_state(conn, session_id):
    ts = _session(conn, session_id)
    if not ts:
        return None
    phase, elapsed, early_remaining = get_phase(conn, ts)
    cfg = get_config(conn)
    roster = q(conn, "SELECT * FROM players WHERE group_id=? ORDER BY first_name, last_name", (ts["group_id"],))
    marked = {r["player_id"]: r for r in q(conn, "SELECT * FROM attendance WHERE training_session_id=?", (session_id,))}
    ids = {p["id"] for p in roster}
    extra = [q1(conn, "SELECT * FROM players WHERE id=?", (pid,)) for pid in marked if pid not in ids]
    players = [_player_card(conn, p, marked.get(p["id"])) for p in roster + [e for e in extra if e]]
    present = sum(1 for p in players if p["status"] == "PRESENT")
    late = sum(1 for p in players if p["status"] == "LATE")
    absent = sum(1 for p in players if p["status"] in ("ABSENT", "EXCUSED"))
    return {
        "ok": True,
        "session_id": session_id,
        "group_name": ts["group_name"],
        "phase": phase,
        "elapsed": elapsed,
        "early_remaining": early_remaining,
        "early_minutes": cfg["early_minutes"],
        "early_points": cfg["early_points"],
        "late_points": cfg["late_points"],
        "counts": {"present": present, "late": late, "absent": absent, "total": len(players),
                   "pending": len(players) - present - late - absent},
        "server_time": now_ksa().strftime("%H:%M:%S"),
        "players": players,
    }
