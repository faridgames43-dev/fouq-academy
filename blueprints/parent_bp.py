from flask import Blueprint, render_template, request, g, abort
from db import get_conn, q, q1
from business.rbac import roles_required, parent_player_ids
from business.subscriptions import get_attendance_eligibility, sync_subscription_statuses
from business.entitlements import get_balances, sync_expirations
from business.assessments import latest_assessment, get_overall, child_label
from business.levels import current_level
from business.points import get_balance

bp = Blueprint("parent_bp", __name__)


@bp.route("/parent")
@roles_required("PARENT")
def hub():
    conn = get_conn()
    player_ids = parent_player_ids(conn, g.user["id"])
    if not player_ids:
        conn.close()
        return render_template("parent_hub.html", children=[], selected=None)

    selected_id = int(request.args.get("player_id", player_ids[0]))
    if selected_id not in player_ids:
        selected_id = player_ids[0]

    children = q(conn, f"""SELECT p.*, gr.name as group_name FROM players p LEFT JOIN groups_ gr ON gr.id=p.group_id
                          WHERE p.id IN ({",".join(["?"] * len(player_ids))})""", tuple(player_ids))

    sync_subscription_statuses(conn, selected_id)
    sync_expirations(conn, selected_id)
    player = q1(conn, "SELECT * FROM players WHERE id=?", (selected_id,))
    balances = get_balances(conn, selected_id)
    eligibility = get_attendance_eligibility(conn, selected_id)
    sub = q1(conn, "SELECT * FROM subscriptions WHERE player_id=? ORDER BY id DESC LIMIT 1", (selected_id,))
    latest = latest_assessment(conn, selected_id)
    overall = None
    if latest:
        overall, _ = get_overall(conn, latest["id"])
    level = current_level(conn, selected_id)
    points = get_balance(conn, selected_id)
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (selected_id,))["c"]
    present = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')", (selected_id,))["c"]
    attendance_pct = round(present / total_sessions * 100, 1) if total_sessions else 0
    last_note = q1(conn, "SELECT notes FROM players WHERE id=?", (selected_id,))
    conn.close()
    return render_template("parent_hub.html", children=children, selected=player, balances=balances,
                            eligibility=eligibility, sub=sub, overall=overall, level=level, points=points,
                            attendance_pct=attendance_pct, child_label=child_label, last_note=last_note)
