import os
import uuid
from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1, DATA_DIR
from business.rbac import permission_required, login_required, parent_player_ids
from business.rewards import (
    list_rewards, get_reward, create_reward, update_reward,
    request_redemption, update_redemption_status, player_redemptions, RewardError,
)
from business.points import get_balance

bp = Blueprint("rewards_bp", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Persistent disk (see players.py's UPLOAD_DIR comment) — not static/, which
# is wiped on every redeploy/restart/spin-down.
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads", "rewards")


def _save_reward_photo(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    try:
        from PIL import Image
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        fname = f"reward-{uuid.uuid4().hex[:10]}.jpg"
        out_path = os.path.join(UPLOAD_DIR, fname)
        img = Image.open(file_storage.stream)
        img = img.convert("RGB")
        img.thumbnail((600, 600))
        img.save(out_path, "JPEG", quality=85)
        return f"/uploads/rewards/{fname}"
    except Exception:
        return None


@bp.route("/uploads/rewards/<path:filename>")
@login_required
def reward_upload(filename):
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)


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


@bp.route("/rewards/new", methods=["GET", "POST"])
@permission_required("manage_rewards")
def new_reward():
    if request.method == "POST":
        f = request.form
        photo_url = _save_reward_photo(request.files.get("photo"))
        try:
            cost = int(f.get("cost", 0))
            stock = int(f.get("stock", 0))
        except ValueError:
            flash("التكلفة والمخزون يجب أن تكون أرقامًا")
            return redirect("/rewards/new")
        conn = get_conn()
        create_reward(conn, f.get("name"), f.get("description"), cost, stock, photo_url, g.user["id"])
        conn.commit()
        conn.close()
        flash("تم إضافة المنتج بنجاح")
        return redirect("/rewards")
    return render_template("reward_form.html", reward=None)


@bp.route("/rewards/<int:reward_id>/edit", methods=["GET", "POST"])
@permission_required("manage_rewards")
def edit_reward(reward_id):
    conn = get_conn()
    reward = get_reward(conn, reward_id)
    if not reward:
        conn.close()
        abort(404)
    if request.method == "POST":
        f = request.form
        photo_url = _save_reward_photo(request.files.get("photo"))
        try:
            cost = int(f.get("cost", 0))
            stock = int(f.get("stock", 0))
        except ValueError:
            conn.close()
            flash("التكلفة والمخزون يجب أن تكون أرقامًا")
            return redirect(f"/rewards/{reward_id}/edit")
        active = 1 if f.get("active") == "on" else 0
        update_reward(conn, reward_id, f.get("name"), f.get("description"), cost, stock, active, photo_url, g.user["id"])
        conn.commit()
        conn.close()
        flash("تم تحديث المنتج بنجاح")
        return redirect("/rewards")
    conn.close()
    return render_template("reward_form.html", reward=reward)


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
    elif g.user["role"] == "PARENT":
        player_id = request.args.get("player_id")
        owned = parent_player_ids(conn, g.user["id"])
        try:
            player_id_int = int(player_id) if player_id else None
        except ValueError:
            player_id_int = None
        if not player_id_int or player_id_int not in owned:
            # real backend check — a parent cannot view/redeem for a child
            # that isn't linked to their own account, no matter what
            # player_id is passed in the query string.
            conn.close()
            abort(403)
        player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id_int,))
    else:
        # staff roles (admin/coach) acting on behalf of a player they manage
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
    redemptions = player_redemptions(conn, player["id"])[:10]
    conn.close()
    return render_template("rewards_store.html", rewards=rewards, balance=balance, player=player,
                            redemptions=redemptions)
