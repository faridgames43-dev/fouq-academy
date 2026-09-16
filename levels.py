"""FOUQ Levels: promotion depends on attendance + development + behavior +
discipline, never on points alone, and always needs supervisor approval."""
from datetime import date, timedelta
from db import q, q1, ex
from business.assessments import latest_assessment, get_overall, get_category_average
from business.audit import log as audit_log


def current_level(conn, player_id):
    row = q1(
        conn,
        """SELECT pl.*, l.name, l.level_order, l.min_attendance_rate, l.min_overall_score, l.min_discipline_score
           FROM player_levels pl JOIN levels l ON l.id = pl.level_id
           WHERE pl.player_id=? AND pl.current=1""",
        (player_id,),
    )
    if row:
        return row
    lvl1 = q1(conn, "SELECT * FROM levels WHERE level_order=1")
    if lvl1:
        pid = ex(conn, "INSERT INTO player_levels(player_id, level_id, current) VALUES (?,?,1)", (player_id, lvl1["id"]))
        return current_level(conn, player_id)
    return None


def next_level(conn, current_order):
    return q1(conn, "SELECT * FROM levels WHERE level_order=?", (current_order + 1,))


def attendance_rate(conn, player_id, window_days=60):
    since = (date.today() - timedelta(days=window_days)).isoformat()
    row = q1(
        conn,
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN a.status IN ('PRESENT','LATE') THEN 1 ELSE 0 END) as attended
           FROM attendance a JOIN training_sessions ts ON ts.id = a.training_session_id
           WHERE a.player_id=? AND ts.session_date >= ? AND a.status != 'FROZEN'""",
        (player_id, since),
    )
    if not row or not row["total"]:
        return 0.0
    return round((row["attended"] or 0) / row["total"] * 100, 1)


def promotion_readiness(conn, player_id):
    cur = current_level(conn, player_id)
    if not cur:
        return None
    nxt = next_level(conn, cur["level_order"])
    if not nxt:
        return {"at_max": True, "level": cur}

    att_rate = attendance_rate(conn, player_id)
    latest = latest_assessment(conn, player_id)
    overall, breakdown = (None, {})
    discipline = None
    if latest:
        overall, breakdown = get_overall(conn, latest["id"])
        discipline = get_category_average(conn, latest["id"], "DISCIPLINE")

    factors = []
    factors.append(("الحضور", att_rate, nxt["min_attendance_rate"], "٪"))
    factors.append(("التقييم العام", overall or 0, nxt["min_overall_score"], ""))
    factors.append(("الانضباط", discipline or 0, nxt["min_discipline_score"], ""))

    ratios = []
    missing = []
    for label, actual, required, unit in factors:
        required = required or 0
        if required <= 0:
            ratios.append(1.0)
            continue
        ratio = min(actual / required, 1.0)
        ratios.append(ratio)
        if actual < required:
            missing.append(f"{label}: {actual}{unit} (المطلوب {required}{unit})")

    readiness_pct = round(sum(ratios) / len(ratios) * 100) if ratios else 0
    return {
        "at_max": False,
        "current_level": cur,
        "next_level": nxt,
        "readiness_pct": readiness_pct,
        "missing": missing,
        "attendance_rate": att_rate,
        "overall_score": overall,
        "discipline_score": discipline,
    }


def approve_promotion(conn, player_id, approver_user_id, approver_role):
    if approver_role not in ("SUPERVISOR", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPER_ADMIN"):
        raise PermissionError("الترقية تحتاج اعتماد المشرف أو الإدارة")
    readiness = promotion_readiness(conn, player_id)
    if not readiness or readiness.get("at_max"):
        raise ValueError("اللاعب في أعلى مستوى بالفعل")
    ex(conn, "UPDATE player_levels SET current=0 WHERE player_id=? AND current=1", (player_id,))
    new_id = ex(conn, "INSERT INTO player_levels(player_id, level_id, approved_by, current) VALUES (?,?,?,1)",
                (player_id, readiness["next_level"]["id"], approver_user_id))
    audit_log(conn, approver_user_id, "APPROVE_PROMOTION", "player_levels", new_id,
              after={"player_id": player_id, "new_level": readiness["next_level"]["name"]})
    return new_id
