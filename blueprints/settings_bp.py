import json
from flask import Blueprint, render_template, request, redirect, g, flash
from db import get_conn, q, q1, ex
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


@bp.route("/settings/categories/new", methods=["POST"])
@permission_required("manage_settings")
def new_category():
    conn = get_conn()
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        conn.close()
        flash("اسم الفئة مطلوب")
        return redirect("/settings")
    ex(conn, "INSERT INTO categories(name, min_age, max_age) VALUES (?,?,?)",
       (name, f.get("min_age") or None, f.get("max_age") or None))
    conn.commit(); conn.close()
    flash("تم إضافة الفئة")
    return redirect("/settings")


@bp.route("/settings/categories/<int:category_id>/edit", methods=["POST"])
@permission_required("manage_settings")
def edit_category(category_id):
    conn = get_conn()
    category = q1(conn, "SELECT * FROM categories WHERE id=?", (category_id,))
    if not category:
        conn.close()
        flash("الفئة غير موجودة")
        return redirect("/settings")
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        conn.close()
        flash("اسم الفئة مطلوب")
        return redirect("/settings")
    active = 1 if f.get("active") == "on" else 0
    ex(conn, "UPDATE categories SET name=?, min_age=?, max_age=?, active=? WHERE id=?",
       (name, f.get("min_age") or None, f.get("max_age") or None, active, category_id))
    audit_log(conn, g.user["id"], "UPDATE_CATEGORY", "categories", category_id,
              before={"name": category["name"]}, after={"name": name, "active": active})
    conn.commit(); conn.close()
    flash("تم تحديث الفئة")
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


@bp.route("/settings/packages/<int:package_id>/edit", methods=["POST"])
@permission_required("manage_packages")
def edit_package(package_id):
    conn = get_conn()
    package = q1(conn, "SELECT * FROM packages WHERE id=?", (package_id,))
    if not package:
        conn.close()
        flash("الباقة غير موجودة")
        return redirect("/settings")
    f = request.form
    try:
        price = float(f.get("price"))
        duration_days = int(f.get("duration_days"))
        sessions_count = int(f.get("sessions_count"))
        days_per_week = int(f.get("days_per_week", 3))
        freeze_policy_days = int(f.get("freeze_policy_days", 7))
        compensation_expiry_days = int(f.get("compensation_expiry_days", 30))
    except (TypeError, ValueError):
        conn.close()
        flash("تأكد أن الأرقام (السعر، المدة، عدد الحصص...) صحيحة")
        return redirect("/settings")
    active = 1 if f.get("active") == "on" else 0
    ex(conn, """UPDATE packages SET name=?, branch_id=?, category_id=?, price=?, duration_days=?,
               sessions_count=?, days_per_week=?, freeze_policy_days=?, compensation_expiry_days=?, active=?
               WHERE id=?""",
       (f.get("name"), f.get("branch_id") or None, f.get("category_id") or None, price, duration_days,
        sessions_count, days_per_week, freeze_policy_days, compensation_expiry_days, active, package_id))
    audit_log(conn, g.user["id"], "UPDATE_PACKAGE", "packages", package_id,
              before={"name": package["name"]}, after={"name": f.get("name"), "active": active})
    conn.commit(); conn.close()
    flash("تم تحديث الباقة")
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


@bp.route("/settings/groups/<int:group_id>/edit", methods=["POST"])
@permission_required("manage_players")
def edit_group(group_id):
    conn = get_conn()
    group = q1(conn, "SELECT * FROM groups_ WHERE id=?", (group_id,))
    if not group:
        conn.close()
        flash("المجموعة غير موجودة")
        return redirect("/settings")
    f = request.form
    active = 1 if f.get("active") == "on" else 0
    ex(conn, "UPDATE groups_ SET name=?, branch_id=?, category_id=?, coach_id=?, active=? WHERE id=?",
       (f.get("name"), f.get("branch_id"), f.get("category_id"), f.get("coach_id") or None, active, group_id))
    audit_log(conn, g.user["id"], "UPDATE_GROUP", "groups_", group_id,
              before={"name": group["name"]}, after={"name": f.get("name"), "active": active})
    conn.commit(); conn.close()
    flash("تم تحديث المجموعة")
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


@bp.route("/settings/coaches/<int:coach_id>/edit", methods=["POST"])
@permission_required("manage_coaches")
def edit_coach(coach_id):
    conn = get_conn()
    coach = q1(conn, "SELECT * FROM coaches WHERE id=?", (coach_id,))
    if not coach:
        conn.close()
        flash("المدرب غير موجود")
        return redirect("/settings")
    f = request.form
    name = f.get("name", "").strip()
    phone = f.get("phone", "").strip()
    if not name or not phone:
        conn.close()
        flash("اسم المدرب وجواله مطلوبان")
        return redirect("/settings")
    branch_id = f.get("branch_id") or None
    active = 1 if f.get("active") == "on" else 0
    ex(conn, "UPDATE coaches SET name=?, phone=?, branch_id=?, active=? WHERE id=?",
       (name, phone, branch_id, active, coach_id))
    # المدرب له حساب دخول مرتبط في users — نحدّث نفس البيانات هناك حتى يبقى
    # اسمه وجواله وفرعه وحالة تفعيله متطابقة بين الجدولين.
    if coach["user_id"]:
        ex(conn, "UPDATE users SET name=?, phone=?, branch_id=?, active=? WHERE id=?",
           (name, phone, branch_id, active, coach["user_id"]))
    audit_log(conn, g.user["id"], "UPDATE_COACH", "coaches", coach_id,
              before={"name": coach["name"]}, after={"name": name, "active": active})
    conn.commit(); conn.close()
    flash("تم تحديث بيانات المدرب")
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


@bp.route("/settings/branches/<int:branch_id>/edit", methods=["POST"])
@permission_required("manage_branches")
def edit_branch(branch_id):
    conn = get_conn()
    branch = q1(conn, "SELECT * FROM branches WHERE id=?", (branch_id,))
    if not branch:
        conn.close()
        flash("الفرع غير موجود")
        return redirect("/settings")
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        conn.close()
        flash("اسم الفرع مطلوب")
        return redirect("/settings")
    active = 1 if f.get("active") == "on" else 0
    ex(conn, "UPDATE branches SET name=?, city=?, address=?, active=? WHERE id=?",
       (name, f.get("city"), f.get("address"), active, branch_id))
    audit_log(conn, g.user["id"], "UPDATE_BRANCH", "branches", branch_id,
              before={"name": branch["name"]}, after={"name": name, "active": active})
    conn.commit(); conn.close()
    flash("تم تحديث الفرع")
    return redirect("/settings")
