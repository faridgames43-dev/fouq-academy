from flask import Blueprint, render_template, request, redirect, g, flash, abort
from datetime import date
from db import get_conn, q, q1
from business.rbac import permission_required
from business import subscriptions as subs
from business import entitlements as ent
from business import legacy as leg
from business import achievements as ach

bp = Blueprint("subscriptions_bp", __name__)


@bp.route("/players/<int:player_id>/subscribe", methods=["GET", "POST"])
@permission_required("manage_subscriptions")
def subscribe(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        abort(404)
    if request.method == "POST":
        f = request.form
        package = q1(conn, "SELECT * FROM packages WHERE id=?", (f.get("package_id"),))
        price = float(f.get("price") or package["price"])
        discount = float(f.get("discount") or 0)
        paid = float(f.get("paid_amount") or 0)
        sub_id = subs.create_subscription(
            conn, player_id, package, f.get("start_date") or date.today().isoformat(),
            price, discount, paid, f.get("payment_method"), f.get("invoice_ref"), f.get("note"),
            user_id=g.user["id"],
        )
        if player["player_type"] == "LEGACY":
            leg.convert_to_fouq(conn, player_id, g.user["id"])
        ach.check_after_renewal(conn, player_id, user_id=g.user["id"])
        conn.commit()
        conn.close()
        flash("تم إنشاء الاشتراك بنجاح. سجل الاشتراكات السابق محفوظ بالكامل.")
        return redirect(f"/players/{player_id}")

    packages = q(conn, "SELECT * FROM packages WHERE active=1 AND (category_id=? OR category_id IS NULL)",
                 (player["category_id"],))
    balances = ent.get_balances(conn, player_id)
    conn.close()
    return render_template("subscribe_form.html", player=player, packages=packages, balances=balances)


@bp.route("/players/<int:player_id>/compensation/add", methods=["GET", "POST"])
@permission_required("manage_compensation")
def add_compensation(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        abort(404)
    from business.settings_lib import get_setting
    default_days = int(get_setting(conn, "compensation_expiry_days", "30"))
    if request.method == "POST":
        f = request.form
        qty = int(f.get("quantity", 1))
        expiry_days = int(f.get("expiry_days") or default_days)
        from datetime import timedelta
        expires_at = (date.today() + timedelta(days=expiry_days)).isoformat()
        reason_code = f.get("reason_code")
        reason_text = f.get("reason_text") or ""
        ent.grant_entitlement(conn, player_id, "COMPENSATION", qty, expires_at=expires_at,
                               reason_code=reason_code, reason_text=reason_text, user_id=g.user["id"])
        conn.commit()
        conn.close()
        flash(f"تم إضافة {qty} حصة تعويضية صالحة حتى {expires_at}")
        return redirect(f"/players/{player_id}")
    conn.close()
    return render_template("compensation_form.html", player=player, default_days=default_days)


ENTITLEMENT_TYPE_LABELS = {
    "REGULAR": "أساسية (من الاشتراك)",
    "COMPENSATION": "تعويضية",
    "BONUS": "إضافية",
    "LEGACY": "سابقة (نادي تواصل الرياضي)",
}


@bp.route("/players/<int:player_id>/entitlements/adjust", methods=["GET", "POST"])
@permission_required("manage_compensation")
def adjust_entitlements(player_id):
    """General-purpose manual add/remove of session credits, of any type and
    any quantity — for corrections that don't fit the subscription or
    compensation flows (e.g. a data-entry fix, a goodwill gesture, syncing
    with a paper record). Always goes through administrative_adjustment so
    a removal can never push a balance negative, and every change is
    ledgered + audit-logged like any other entitlement mutation."""
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        conn.close()
        abort(404)
    if request.method == "POST":
        f = request.form
        etype = f.get("etype")
        action = f.get("action")
        reason = (f.get("reason") or "").strip()
        try:
            qty = int(f.get("quantity", 0))
        except (TypeError, ValueError):
            qty = 0
        if etype not in ENTITLEMENT_TYPE_LABELS or action not in ("add", "remove") or qty <= 0:
            conn.close()
            flash("تأكد من اختيار نوع الحصص والإجراء وكمية صحيحة أكبر من صفر")
            return redirect(f"/players/{player_id}/entitlements/adjust")
        if not reason:
            reason = "إضافة يدوية من الإدارة" if action == "add" else "خصم يدوي من الإدارة"
        delta = qty if action == "add" else -qty
        try:
            ent.administrative_adjustment(conn, player_id, etype, delta, reason, g.user["id"])
        except ent.EntitlementError as e:
            conn.close()
            flash(str(e))
            return redirect(f"/players/{player_id}/entitlements/adjust")
        conn.commit()
        conn.close()
        verb = "إضافة" if action == "add" else "خصم"
        flash(f"تم {verb} {qty} حصة ({ENTITLEMENT_TYPE_LABELS[etype]}) — {player['first_name']} {player['last_name']}")
        return redirect(f"/players/{player_id}")
    balances = ent.get_balances(conn, player_id)
    conn.close()
    return render_template("entitlement_adjust_form.html", player=player, balances=balances,
                            type_labels=ENTITLEMENT_TYPE_LABELS)


@bp.route("/players/<int:player_id>/subscriptions/<int:sub_id>/freeze", methods=["POST"])
@permission_required("manage_subscriptions")
def freeze(player_id, sub_id):
    conn = get_conn()
    days = int(request.form.get("days", 7))
    subs.freeze_subscription(conn, sub_id, days, request.form.get("reason", "تجميد بطلب ولي الأمر"), g.user["id"])
    conn.commit(); conn.close()
    flash("تم تجميد الاشتراك")
    return redirect(f"/players/{player_id}")


@bp.route("/players/<int:player_id>/subscriptions/<int:sub_id>/unfreeze", methods=["POST"])
@permission_required("manage_subscriptions")
def unfreeze(player_id, sub_id):
    conn = get_conn()
    subs.unfreeze_subscription(conn, sub_id, g.user["id"])
    conn.commit(); conn.close()
    flash("تم إلغاء التجميد")
    return redirect(f"/players/{player_id}")
