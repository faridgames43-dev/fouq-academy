"""Unified "الحسابات" hub: one place to create a login account of any type
(لاعب / مدرب / ولي أمر / إداري) instead of them being scattered across
/players/new and /settings. Also hosts the one-time "بدء من جديد" reset
action that clears demo/test players+coaches+parents before real onboarding."""
from flask import Blueprint, render_template, request, redirect, g, flash, Response
from werkzeug.security import generate_password_hash
from db import get_conn, q, q1, ex
from business.rbac import login_required, permission_required, roles_required, ADMIN_ROLES
from business.accounts import create_user_account, find_parent_by_phone, AccountError
from business.audit import log as audit_log
from business import reset_demo
import json

bp = Blueprint("accounts_bp", __name__)

STAFF_ROLE_LABELS = {
    "SUPER_ADMIN": "إدارة عليا", "PROJECT_MANAGER": "مدير مشروع",
    "BRANCH_MANAGER": "مدير فرع", "SUPERVISOR": "مشرف تشغيلي",
}


@bp.route("/accounts")
@login_required
def hub():
    if g.user["role"] not in ADMIN_ROLES:
        from flask import abort
        abort(403)
    conn = get_conn()
    branches = q(conn, "SELECT * FROM branches WHERE active=1")
    parents = q(conn, "SELECT * FROM parents ORDER BY name")
    staff = q(conn, "SELECT * FROM users WHERE role IN ('SUPER_ADMIN','PROJECT_MANAGER','BRANCH_MANAGER','SUPERVISOR') ORDER BY role, name")
    can_manage_users = g.user["role"] in ("SUPER_ADMIN", "PROJECT_MANAGER")
    conn.close()
    return render_template("accounts_hub.html", branches=branches, parents=parents, staff=staff,
                            can_manage_users=can_manage_users, staff_role_labels=STAFF_ROLE_LABELS)


@bp.route("/accounts/parents/new", methods=["POST"])
@permission_required("manage_players")
def new_parent():
    conn = get_conn()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip() or None
    if not name:
        conn.close()
        flash("اسم ولي الأمر مطلوب")
        return redirect("/accounts")
    existing = find_parent_by_phone(conn, phone)
    if existing:
        conn.close()
        flash(f"يوجد حساب ولي أمر بنفس رقم الجوال مسبقًا: {existing['name']}")
        return redirect("/accounts")
    uid, password = create_user_account(conn, name, "PARENT", phone=phone)
    ex(conn, "INSERT INTO parents(user_id, name, phone) VALUES (?,?,?)", (uid, name, phone))
    audit_log(conn, g.user["id"], "CREATE_PARENT_ACCOUNT", "users", uid, after={"name": name, "phone": phone})
    conn.commit(); conn.close()
    flash(f"تم إنشاء حساب ولي الأمر: {name}")
    flash(f"🔑 بيانات الدخول — الجوال: {phone} / كلمة المرور: {password} (تُعرض مرة واحدة فقط)")
    return redirect("/accounts")


@bp.route("/accounts/staff/new", methods=["POST"])
@permission_required("manage_users")
def new_staff():
    conn = get_conn()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    role = request.form.get("role")
    branch_id = request.form.get("branch_id") or None
    if role not in STAFF_ROLE_LABELS:
        conn.close()
        flash("دور غير صالح")
        return redirect("/accounts")
    if not name or (not email and not phone):
        conn.close()
        flash("الاسم والبريد أو الجوال مطلوبة")
        return redirect("/accounts")
    uid, password = create_user_account(conn, name, role, phone=phone, email=email, branch_id=branch_id)
    audit_log(conn, g.user["id"], "CREATE_STAFF_ACCOUNT", "users", uid, after={"name": name, "role": role})
    conn.commit(); conn.close()
    flash(f"تم إنشاء حساب {STAFF_ROLE_LABELS[role]}: {name}")
    flash(f"🔑 بيانات الدخول — {email or phone} / كلمة المرور: {password} (تُعرض مرة واحدة فقط)")
    return redirect("/accounts")


@bp.route("/accounts/reset-data", methods=["GET"])
@roles_required("SUPER_ADMIN")
def reset_data_confirm():
    conn = get_conn()
    c = reset_demo.counts(conn)
    conn.close()
    return render_template("reset_data_confirm.html", counts=c)


@bp.route("/accounts/reset-data/backup.json", methods=["GET"])
@roles_required("SUPER_ADMIN")
def reset_data_backup():
    conn = get_conn()
    backup = reset_demo.build_backup(conn)
    conn.close()
    body = json.dumps(backup, ensure_ascii=False, default=str, indent=2)
    return Response(body, mimetype="application/json",
                     headers={"Content-Disposition": "attachment; filename=fouq-backup-before-reset.json"})


@bp.route("/accounts/reset-data", methods=["POST"])
@roles_required("SUPER_ADMIN")
def reset_data_execute():
    confirm_text = request.form.get("confirm_text", "").strip()
    if confirm_text != "حذف نهائي":
        flash("لم يتم التنفيذ — يجب كتابة عبارة التأكيد بالضبط")
        return redirect("/accounts/reset-data")
    conn = get_conn()
    summary = reset_demo.execute_reset(conn, g.user["id"])
    conn.commit()
    conn.close()
    flash(f"✅ تم الحذف: {summary['players']} لاعب، {summary['coaches']} مدرب، {summary['parents']} ولي أمر. "
          f"الحسابات الإدارية والفروع والمجموعات والباقات بقيت كما هي.")
    return redirect("/accounts")
