import json
from flask import Blueprint, render_template, request, redirect, g, flash
from db import get_conn, q, ex
from business.rbac import permission_required
from business.settings_lib import get_setting, get_setting_json, set_setting
from business.audit import log as audit_log

bp = Blueprint("settings_bp", __name__)


@bp.route("/settings", methods=["GET"])
@permission_required("manage_settings")
def index():
    conn = get_conn()
    branches = q(conn, "SELECT * FROM branches")
    categories = q(conn, "SELECT * FROM categories")
    groups = q(conn, "SELECT g.*, b.name as branch_name, c.name as category_name, co.name as coach_name FROM groups_ g LEFT JOIN branches b ON b.id=g.branch_id LEFT JOIN categories c ON c.id=g.category_id LEFT JOIN coaches co ON co.id=g.coach_id")
    coaches = q(conn, "SELECT * FROM coaches")
    packages = q(conn, "SELECT p.*, c.name as category_name FROM packages p LEFT JOIN categories c ON c.id=p.category_id")
    weights = get_setting_json(conn, "assessment_weights")
    comp_days = get_setting(conn, "compensation_expiry_days")
    coach_cap = get_setting(conn, "coach_daily_points_cap")
    conn.close()
    return render_template("settings.html", branches=branches, categories=categories, groups=groups,
                            coaches=coaches, packages=packages, weights=weights, comp_days=comp_days,
                            coach_cap=coach_cap)


@bp.route("/settings/policies", methods=["POST"])
@permission_required("manage_settings")
def update_policies():
    conn = get_conn()
    set_setting(conn, "compensation_expiry_days", request.form.get("compensation_expiry_days", "30"))
    set_setting(conn, "coach_daily_points_cap", request.form.get("coach_daily_points_cap", "20"))
    weights = {
        "SKILL": int(request.form.get("w_skill", 40)), "FITNESS": int(request.form.get("w_fitness", 25)),
        "BEHAVIOR": int(request.form.get("w_behavior", 20)), "DISCIPLINE": int(request.form.get("w_discipline", 15)),
    }
    set_setting(conn, "assessment_weights", json.dumps(weights, ensure_ascii=False))
    audit_log(conn, g.user["id"], "UPDATE_SETTINGS", "settings", after=weights)
    conn.commit(); conn.close()
    flash("تم حفظ السياسات")
    return redirect("/settings")


@bp.route("/settings/packages/new", methods=["POST"])
@permission_required("manage_packages")
def new_package():
    conn = get_conn()
    f = request.form
    ex(conn, """INSERT INTO packages(name, branch_id, category_id, price, duration_days, sessions_count,
               days_per_week, freeze_policy_days, compensation_expiry_days) VALUES (?,?,?,?,?,?,?,?,?)""",
       (f.get("name"), f.get("branch_id") or None, f.get("category_id") or None, float(f.get("price")),
        int(f.get("duration_days")), int(f.get("sessions_count")), int(f.get("days_per_week", 3)),
        int(f.get("freeze_policy_days", 7)), int(f.get("compensation_expiry_days", 30))))
    conn.commit(); conn.close()
    flash("تم إنشاء الباقة")
    return redirect("/settings")


@bp.route("/settings/groups/new", methods=["POST"])
@permission_required("manage_players")
def new_group():
    conn = get_conn()
    f = request.form
    ex(conn, "INSERT INTO groups_(name, branch_id, category_id, coach_id) VALUES (?,?,?,?)",
       (f.get("name"), f.get("branch_id"), f.get("category_id"), f.get("coach_id") or None))
    conn.commit(); conn.close()
    flash("تم إنشاء المجموعة")
    return redirect("/settings")


@bp.route("/settings/coaches/new", methods=["POST"])
@permission_required("manage_coaches")
def new_coach():
    conn = get_conn()
    f = request.form
    from werkzeug.security import generate_password_hash
    uid = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
              (f.get("name"), f.get("email") or None, f.get("phone"), generate_password_hash("Fouq@2026"),
               "COACH", f.get("branch_id")))
    ex(conn, "INSERT INTO coaches(user_id, name, phone, branch_id) VALUES (?,?,?,?)",
       (uid, f.get("name"), f.get("phone"), f.get("branch_id")))
    conn.commit(); conn.close()
    flash("تم إضافة المدرب (كلمة المرور الافتراضية: Fouq@2026)")
    return redirect("/settings")


@bp.route("/settings/branches/new", methods=["POST"])
@permission_required("manage_branches")
def new_branch():
    conn = get_conn()
    f = request.form
    ex(conn, "INSERT INTO branches(name, city, address) VALUES (?,?,?)", (f.get("name"), f.get("city"), f.get("address")))
    conn.commit(); conn.close()
    flash("تم إضافة الفرع")
    return redirect("/settings")
