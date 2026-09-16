from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1
from business.rbac import permission_required, login_required
from business.rewards import list_rewards, request_redemption, update_redemption_status, RewardError
from business.points import get_balance

bp = Blueprint("rewards_bp", __name__)


@bp.route("/rewards")
@permission_required("manage_rewards")
def admin_index():
    conn = get_conn()
    rewards = list_rewards(conn, active_only=False)
    redemptions = q(conn, """SELECT rr.*, r.name as reward_name, r.cost, p.first_name, p.last_name, p.player_code
                             FROM reward_redemptions rr JOIN rewards r ON r.id=rr.reward_id
                             JOIN players p ON p.id=rr.player_id ORDER BY rr.id DESC""")
    conn.close()
    return render_template("rewards_admin.html", rewards=rewards, redemptions=redemptions)


@bp.route("/rewards/redemptions/<int:redemption_id>/<status>", methods=["POST"])
@permission_required("decide_redemption")
def decide(redemption_id, status):
    conn = get_conn()
    try:
        update_redemption_status(conn, redemption_id, status, g.user["id"])
        conn.commit()
        flash("تم تحديث حالة الطلب")
    except RewardError as e:
        flash(str(e))
    conn.close()
    return redirect("/rewards")


@bp.route("/rewards/store", methods=["GET", "POST"])
@login_required
def store():
    conn = get_conn()
    if g.user["role"] == "PLAYER":
        player = q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    else:
        player_id = request.args.get("player_id")
        player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,)) if player_id else None
    if not player:
        conn.close()
        abort(404)

    if request.method == "POST":
        try:
            request_redemption(conn, player["id"], request.form.get("reward_id"), g.user["id"])
            conn.commit()
            flash("تم إرسال طلب الاستبدال — بانتظار موافقة الإدارة")
        except RewardError as e:
            flash(str(e))
        conn.close()
        return redirect(request.path if g.user["role"] == "PLAYER" else f"/rewards/store?player_id={player['id']}")

    rewards = list_rewards(conn)
    balance = get_balance(conn, player["id"])
    conn.close()
    return render_template("rewards_store.html", rewards=rewards, balance=balance, player=player)
