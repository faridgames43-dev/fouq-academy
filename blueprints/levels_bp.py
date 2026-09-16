from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1
from business.rbac import permission_required, login_required
from business.levels import approve_promotion, promotion_readiness, current_level
from business import achievements as ach

bp = Blueprint("levels_bp", __name__)


@bp.route("/levels")
@login_required
def index():
    conn = get_conn()
    levels = q(conn, "SELECT * FROM levels ORDER BY level_order")
    for lv in levels:
        lv["players_count"] = q1(conn, """SELECT COUNT(*) c FROM player_levels WHERE level_id=? AND current=1""",
                                  (lv["id"],))["c"]
    conn.close()
    return render_template("levels.html", levels=levels)


@bp.route("/players/<int:player_id>/promote", methods=["POST"])
@permission_required("approve_promotion")
def promote(player_id):
    conn = get_conn()
    try:
        approve_promotion(conn, player_id, g.user["id"], g.user["role"])
        ach.check_after_promotion(conn, player_id, g.user["id"])
        conn.commit()
        flash("تم اعتماد الترقية بنجاح 🎉")
    except Exception as e:
        flash(f"تعذّرت الترقية: {e}")
    conn.close()
    return redirect(f"/players/{player_id}")
