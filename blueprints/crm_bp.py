from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1
from business.rbac import permission_required
from business import crm

bp = Blueprint("crm_bp", __name__)

STATUS_LABELS = {
    "NEW": "جديد", "CONTACTED": "تم التواصل", "TRIAL_BOOKED": "حجز تجربة", "TRIAL_ATTENDED": "حضر التجربة",
    "NO_SHOW": "لم يحضر", "INTERESTED": "مهتم", "PAID": "دفع/تحوّل لعضوية", "LOST": "فقدناه", "FOLLOW_UP": "متابعة",
}


@bp.route("/leads", methods=["GET", "POST"])
@permission_required("manage_crm")
def index():
    conn = get_conn()
    if request.method == "POST":
        f = request.form
        crm.create_lead(conn, f.get("parent_name"), f.get("child_name"), f.get("child_age") or None,
                         f.get("phone"), f.get("source"), f.get("campaign"), g.user["id"], g.user["branch_id"],
                         f.get("notes"))
        conn.commit()
        conn.close()
        flash("تم تسجيل العميل المحتمل")
        return redirect("/leads")
    leads = q(conn, "SELECT * FROM leads ORDER BY id DESC")
    groups = q(conn, "SELECT * FROM groups_")
    conn.close()
    return render_template("leads_board.html", leads=leads, groups=groups, status_labels=STATUS_LABELS)


@bp.route("/leads/<int:lead_id>/status", methods=["POST"])
@permission_required("manage_crm")
def update_status(lead_id):
    conn = get_conn()
    crm.update_lead_status(conn, lead_id, request.form.get("status"), g.user["id"], request.form.get("note"))
    conn.commit(); conn.close()
    flash("تم تحديث حالة العميل")
    return redirect("/leads")


@bp.route("/leads/<int:lead_id>/book_trial", methods=["POST"])
@permission_required("manage_crm")
def book_trial(lead_id):
    conn = get_conn()
    crm.book_trial(conn, lead_id, request.form.get("trial_date"), request.form.get("group_id"), g.user["id"])
    conn.commit(); conn.close()
    flash("تم حجز حصة تجربة")
    return redirect("/leads")
