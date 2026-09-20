from flask import Blueprint, render_template, request, redirect, flash, g, abort
from db import get_conn, q, q1, ex
from business.rbac import roles_required, parent_player_ids
from business.subscriptions import get_attendance_eligibility, sync_subscription_statuses
from business.entitlements import get_balances, sync_expirations
from business.assessments import latest_assessment, get_overall, child_label
from business.levels import current_level
from business.points import get_balance
from business.rewards import nearest_reward
from business.notifications import create_notification
from business import achievements as ach

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

    ach.check_tenure(conn, selected_id, g.user["id"])
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
    last_achievement = q1(conn, """SELECT a.* FROM player_achievements pa JOIN achievements a ON a.id=pa.achievement_id
                                   WHERE pa.player_id=? ORDER BY pa.earned_at DESC LIMIT 1""", (selected_id,))
    reward_next = nearest_reward(conn, points)
    days_left = None
    if sub and sub["end_date"]:
        from datetime import date
        try:
            days_left = (date.fromisoformat(sub["end_date"]) - date.today()).days
        except Exception:
            days_left = None
    conn.commit()
    conn.close()
    return render_template(
        "parent_hub.html", children=children, selected=player, balances=balances,
        eligibility=eligibility, sub=sub, overall=overall, level=level, points=points,
        attendance_pct=attendance_pct, child_label=child_label, last_note=last_note,
        last_achievement=last_achievement, reward_next=reward_next, days_left=days_left,
    )


@bp.route("/parent/request-renewal", methods=["POST"])
@roles_required("PARENT")
def request_renewal():
    """The academy has no online payment gateway connected yet (see
    FEATURES.md), so a parent's 'renew now' click creates a real renewal
    request the staff sees immediately in /renewals and /notifications,
    rather than linking to the staff-only subscription form."""
    conn = get_conn()
    player_id = request.form.get("player_id")
    owned = parent_player_ids(conn, g.user["id"])
    try:
        player_id_int = int(player_id)
    except (TypeError, ValueError):
        player_id_int = None
    if not player_id_int or player_id_int not in owned:
        conn.close()
        abort(403)
    player = q1(conn, "SELECT first_name, last_name FROM players WHERE id=?", (player_id_int,))
    ex(conn, "INSERT INTO renewal_notes(player_id, contacted_by, note, stage) VALUES (?,?,?,?)",
       (player_id_int, g.user["id"], "طلب تجديد من ولي الأمر عبر حسابه", "PARENT_REQUESTED"))
    create_notification(conn, "PARENT_RENEWAL_REQUEST",
                         f"ولي أمر {player['first_name']} {player['last_name']} طلب تجديد الاشتراك",
                         "تواصل معه لإتمام التجديد", player_id=player_id_int, dedupe=False)
    conn.commit()
    conn.close()
    flash("تم إرسال طلب التجديد للإدارة — سيتم التواصل معك قريبًا 📞")
    return redirect(f"/parent?player_id={player_id_int}")
