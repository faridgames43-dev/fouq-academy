"""Rule-based (not AI, not random) achievement checks - run after the
events that could trigger them: attendance, points, renewal, levels."""
from db import q, q1, ex
from business.points import award_points
from business.audit import log as audit_log


def _award_if_new(conn, player_id, code, user_id=None):
    ach = q1(conn, "SELECT * FROM achievements WHERE code=?", (code,))
    if not ach:
        return None
    existing = q1(conn, "SELECT id FROM player_achievements WHERE player_id=? AND achievement_id=?",
                  (player_id, ach["id"]))
    if existing:
        return None
    pa_id = ex(conn, "INSERT INTO player_achievements(player_id, achievement_id) VALUES (?,?)",
               (player_id, ach["id"]))
    if ach["points_reward"]:
        award_points(conn, player_id, ach["points_reward"], f"إنجاز: {ach['name']}", "ACHIEVEMENT", user_id)
    audit_log(conn, user_id, "AWARD_ACHIEVEMENT", "player_achievements", pa_id,
              after={"player_id": player_id, "achievement": code})
    return pa_id


def check_after_attendance(conn, player_id, user_id=None):
    total_present = q1(
        conn, "SELECT COUNT(*) as c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')",
        (player_id,),
    )["c"]
    newly = []
    if total_present >= 1:
        if _award_if_new(conn, player_id, "FIRST_SESSION", user_id):
            newly.append("FIRST_SESSION")
    if total_present >= 10:
        if _award_if_new(conn, player_id, "COMMITTED_10", user_id):
            newly.append("COMMITTED_10")

    # 5 consecutive attended sessions with no ABSENT in between (last 5 chronological)
    last5 = q(
        conn,
        """SELECT a.status FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
           WHERE a.player_id=? ORDER BY ts.session_date DESC, a.id DESC LIMIT 5""",
        (player_id,),
    )
    if len(last5) == 5 and all(r["status"] in ("PRESENT", "LATE") for r in last5):
        if _award_if_new(conn, player_id, "STREAK_5", user_id):
            newly.append("STREAK_5")
    return newly


def check_after_points(conn, player_id, user_id=None):
    from business.points import get_balance
    bal = get_balance(conn, player_id)
    newly = []
    if bal >= 100:
        if _award_if_new(conn, player_id, "FIRST_100", user_id):
            newly.append("FIRST_100")
    return newly


def check_after_renewal(conn, player_id, user_id=None):
    count = q1(conn, "SELECT COUNT(*) as c FROM subscriptions WHERE player_id=?", (player_id,))["c"]
    newly = []
    if count >= 3:
        if _award_if_new(conn, player_id, "LOYAL_3X", user_id):
            newly.append("LOYAL_3X")
    return newly


def check_after_promotion(conn, player_id, user_id=None):
    count = q1(conn, "SELECT COUNT(*) as c FROM player_levels WHERE player_id=?", (player_id,))["c"]
    newly = []
    if count >= 2:
        if _award_if_new(conn, player_id, "FIRST_PROMOTION", user_id):
            newly.append("FIRST_PROMOTION")
    return newly


def check_development_leap(conn, player_id, prev_overall, new_overall, user_id=None):
    newly = []
    if prev_overall is not None and new_overall is not None and (new_overall - prev_overall) >= 15:
        if _award_if_new(conn, player_id, "DEVELOPMENT_LEAP", user_id):
            newly.append("DEVELOPMENT_LEAP")
    return newly


def award_manual(conn, player_id, code, user_id):
    return _award_if_new(conn, player_id, code, user_id)
