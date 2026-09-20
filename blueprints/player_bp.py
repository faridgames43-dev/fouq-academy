from flask import Blueprint, render_template, g, abort, request
from db import get_conn, q, q1
from business.rbac import roles_required, login_required, parent_player_ids
from business.subscriptions import get_attendance_eligibility
from business.entitlements import get_balances
from business.levels import current_level, promotion_readiness
from business.points import get_balance
from business.assessments import latest_assessment, get_overall, child_label, player_development_timeline
from business.rewards import nearest_reward, player_redemptions
from business import achievements as ach
from business import challenges as chal

bp = Blueprint("player_bp", __name__)


@bp.route("/me")
@roles_required("PLAYER")
def hub():
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    if not player:
        conn.close()
        abort(404)
    ach.check_tenure(conn, player["id"], g.user["id"])
    conn.commit()
    balances = get_balances(conn, player["id"])
    eligibility = get_attendance_eligibility(conn, player["id"])
    level = current_level(conn, player["id"])
    readiness = promotion_readiness(conn, player["id"])
    points = get_balance(conn, player["id"])
    latest = latest_assessment(conn, player["id"])
    overall = None
    if latest:
        overall, _ = get_overall(conn, latest["id"])
    achievements = q(conn, """SELECT a.* FROM player_achievements pa JOIN achievements a ON a.id=pa.achievement_id
                              WHERE pa.player_id=? ORDER BY pa.earned_at DESC LIMIT 6""", (player["id"],))
    last_achievement = achievements[0] if achievements else None
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (player["id"],))["c"]
    present = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')", (player["id"],))["c"]
    attendance_pct = round(present / total_sessions * 100, 1) if total_sessions else 0

    current_challenge = chal.get_current_challenge(conn, player["id"])
    challenge_progress_val, challenge_target = (0, 0)
    if current_challenge:
        challenge_progress_val, challenge_target = chal.challenge_progress(conn, player["id"], current_challenge)
    reward_next = nearest_reward(conn, points)

    conn.commit()
    conn.close()
    return render_template(
        "player_hub.html", player=player, balances=balances, eligibility=eligibility,
        level=level, readiness=readiness, points=points, overall=overall,
        achievements=achievements, last_achievement=last_achievement,
        attendance_pct=attendance_pct, child_label=child_label,
        current_challenge=current_challenge, challenge_progress_val=challenge_progress_val,
        challenge_target=challenge_target, reward_next=reward_next,
    )


@bp.route("/me/journey")
@login_required
def journey():
    conn = get_conn()
    if g.user["role"] == "PLAYER":
        player = q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    elif g.user["role"] == "PARENT":
        pid = request.args.get("player_id")
        owned = parent_player_ids(conn, g.user["id"])
        try:
            pid = int(pid) if pid else None
        except ValueError:
            pid = None
        if not pid or pid not in owned:
            conn.close()
            abort(403)
        player = q1(conn, "SELECT * FROM players WHERE id=?", (pid,))
    elif g.user["role"] in ("SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH"):
        pid = request.args.get("player_id")
        player = q1(conn, "SELECT * FROM players WHERE id=?", (pid,)) if pid else None
    else:
        player = None
    if not player:
        conn.close()
        abort(404)

    pid = player["id"]
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (pid,))["c"]
    present = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')", (pid,))["c"]
    attendance_pct = round(present / total_sessions * 100, 1) if total_sessions else 0
    training_hours = round(present * 1.5, 1)
    level_history = q(conn, """SELECT pl.achieved_at, l.name, l.level_order FROM player_levels pl
                               JOIN levels l ON l.id=pl.level_id WHERE pl.player_id=? ORDER BY pl.achieved_at""", (pid,))
    level = current_level(conn, pid)
    points_earned = q1(conn, "SELECT COALESCE(SUM(amount),0) c FROM points_transactions WHERE player_id=? AND amount>0",
                        (pid,))["c"]
    points_balance = get_balance(conn, pid)
    achievements = q(conn, """SELECT a.*, pa.earned_at FROM player_achievements pa JOIN achievements a ON a.id=pa.achievement_id
                              WHERE pa.player_id=? ORDER BY pa.earned_at""", (pid,))
    redemptions = player_redemptions(conn, pid)
    dev_timeline = player_development_timeline(conn, pid)
    conn.close()
    return render_template(
        "player_journey.html", player=player, attendance_pct=attendance_pct, total_sessions=total_sessions,
        training_hours=training_hours, level_history=level_history, level=level, points_earned=points_earned,
        points_balance=points_balance, achievements=achievements, redemptions=redemptions,
        dev_timeline=dev_timeline, child_label=child_label,
    )
