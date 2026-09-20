from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q1
from business.rbac import permission_required
from business.points import (
    award_points, coach_points_granted_today, cancel_points_transaction, get_history,
    PointsError, ADD_REASONS, SUBTRACT_REASONS,
)
from business.settings_lib import get_setting
from business import achievements as ach
from business import challenges as chal

bp = Blueprint("points_bp", __name__)

CATEGORY_LABELS = {
    "ATTENDANCE": "الحضور المنتظم", "DEVELOPMENT": "التطور", "BEHAVIOR": "السلوك والانضباط",
    "CHALLENGE": "تحدي", "ACHIEVEMENT": "إنجاز", "RENEWAL": "تجديد", "REFERRAL": "إحالة",
    "ADMIN": "منح إداري", "DISCIPLINE": "الانضباط",
}


def _apply_points(conn, player_id, amount, reason, category, note=None):
    """Shared entry point for every points award in the app (quick reasons,
    custom form, or the coach's end-of-session screen) so the coach daily
    cap, achievement checks and challenge checks always run together."""
    cap = int(get_setting(conn, "coach_daily_points_cap", "20"))
    if g.user["role"] == "COACH" and amount > 0:
        already = coach_points_granted_today(conn, g.user["id"], player_id)
        if already + amount > cap:
            raise PointsError(f"لا يمكن للمدرب منح أكثر من {cap} نقطة يوميًا لكل لاعب (تم منح {already} اليوم)")
    award_points(conn, player_id, amount, reason, category, g.user["id"], note=note)
    ach.check_after_points(conn, player_id, g.user["id"])
    chal.check_and_complete(conn, player_id, g.user["id"])
    if reason == "الروح الرياضية":
        ach.award_manual(conn, player_id, "TEAM_SPIRIT", g.user["id"])
    if reason == "لاعب الحصة":
        ach.award_manual(conn, player_id, "PLAYER_OF_SESSION", g.user["id"])


@bp.route("/players/<int:player_id>/points/add", methods=["GET", "POST"])
@permission_required("grant_points")
def add_points(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        conn.close()
        abort(404)
    cap = int(get_setting(conn, "coach_daily_points_cap", "20"))
    granted_today = coach_points_granted_today(conn, g.user["id"], player_id) if g.user["role"] == "COACH" else 0

    if request.method == "POST":
        try:
            amount = int(request.form.get("amount", 0))
        except ValueError:
            amount = 0
        reason = (request.form.get("reason") or "").strip()
        category = request.form.get("category") or "ADMIN"
        note = (request.form.get("note") or "").strip() or None
        if not reason:
            reason = CATEGORY_LABELS.get(category, "منح رصيد")
        if amount == 0:
            conn.close()
            flash("عدد النقاط لا يمكن أن يكون صفرًا")
            return redirect(f"/players/{player_id}/points/add")
        try:
            _apply_points(conn, player_id, amount, reason, category, note)
        except PointsError as e:
            conn.close()
            flash(str(e))
            return redirect(f"/players/{player_id}/points/add")
        conn.commit()
        conn.close()
        flash(f"تم {'إضافة' if amount > 0 else 'خصم'} {abs(amount)} من رصيد فوق — السبب: {reason}")
        return redirect(f"/players/{player_id}")

    history = get_history(conn, player_id, 25)
    conn.close()
    return render_template(
        "points_form.html", player=player, cap=cap, granted_today=granted_today,
        add_reasons=ADD_REASONS, subtract_reasons=SUBTRACT_REASONS, categories=CATEGORY_LABELS,
        history=history,
    )


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
