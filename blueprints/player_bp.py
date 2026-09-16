from flask import Blueprint, render_template, g, abort
from db import get_conn, q, q1
from business.rbac import roles_required
from business.subscriptions import get_attendance_eligibility
from business.entitlements import get_balances
from business.levels import current_level, promotion_readiness
from business.points import get_balance
from business.assessments import latest_assessment, get_overall, child_label

bp = Blueprint("player_bp", __name__)


@bp.route("/me")
@roles_required("PLAYER")
def hub():
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    if not player:
        conn.close()
        abort(404)
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
    conn.close()
    return render_template("player_hub.html", player=player, balances=balances, eligibility=eligibility,
                            level=level, readiness=readiness, points=points, overall=overall,
                            achievements=achievements, last_achievement=last_achievement,
                            attendance_pct=attendance_pct, child_label=child_label)
