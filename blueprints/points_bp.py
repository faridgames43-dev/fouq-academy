from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q1
from business.rbac import permission_required
from business.points import award_points, coach_points_granted_today, cancel_points_transaction, PointsError
from business.settings_lib import get_setting
from business import achievements as ach

bp = Blueprint("points_bp", __name__)

CATEGORY_LABELS = {
    "ATTENDANCE": "الحضور المنتظم", "DEVELOPMENT": "التطور", "BEHAVIOR": "السلوك والانضباط",
    "CHALLENGE": "تحدي", "ACHIEVEMENT": "إنجاز", "RENEWAL": "تجديد", "REFERRAL": "إحالة", "ADMIN": "منح إداري",
}


@bp.route("/players/<int:player_id>/points/add", methods=["GET", "POST"])
@permission_required("grant_points")
def add_points(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        abort(404)
    cap = int(get_setting(conn, "coach_daily_points_cap", "20"))
    if request.method == "POST":
        amount = int(request.form.get("amount", 0))
        category = request.form.get("category", "ADMIN")
        reason = request.form.get("reason") or CATEGORY_LABELS.get(category, "منح رصيد")
        if g.user["role"] == "COACH":
            already = coach_points_granted_today(conn, g.user["id"], player_id)
            if already + amount > cap:
                conn.close()
                flash(f"لا يمكن للمدرب منح أكثر من {cap} نقطة يوميًا لكل لاعب (تم منح {already} اليوم)")
                return redirect(f"/players/{player_id}")
        try:
            award_points(conn, player_id, amount, reason, category, g.user["id"])
            ach.check_after_points(conn, player_id, g.user["id"])
        except PointsError as e:
            conn.close()
            flash(str(e))
            return redirect(f"/players/{player_id}")
        conn.commit()
        conn.close()
        flash(f"تم إضافة {amount} إلى رصيد فوق")
        return redirect(f"/players/{player_id}")
    conn.close()
    return render_template("points_form.html", player=player, cap=cap, categories=CATEGORY_LABELS)


@bp.route("/players/<int:player_id>/points/<int:txn_id>/cancel", methods=["POST"])
@permission_required("grant_points")
def cancel_points(player_id, txn_id):
    conn = get_conn()
    try:
        cancel_points_transaction(conn, txn_id, g.user["id"])
        conn.commit()
        flash("تم إلغاء العملية وعكس الرصيد")
    except PointsError as e:
        flash(str(e))
    conn.close()
    return redirect(f"/players/{player_id}")
