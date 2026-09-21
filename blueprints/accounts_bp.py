"""Unified "الحسابات" hub: one place to create a login account of any type
(لاعب / مدرب / ولي أمر / إداري) instead of them being scattered across
/players/new and /settings. Also hosts the one-time "بدء من جديد" reset
action that clears demo/test players+coaches+parents before real onboarding."""
from flask import Blueprint, render_template, request, redirect, g, flash, Response, send_file
from werkzeug.security import generate_password_hash
from db import get_conn, q, q1, ex
from business.rbac import login_required, permission_required, roles_required, ADMIN_ROLES
from business.accounts import create_user_account, find_parent_by_phone, AccountError, generate_password
from business.audit import log as audit_log
from business import reset_demo
from business import bulk_import
from business.pdf_export import build_credentials_pdf
import json
import io

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


@bp.route("/accounts/import", methods=["GET"])
@permission_required("manage_players")
def import_players_form():
    suggested_password = generate_password()
    return render_template("accounts_import.html", suggested_password=suggested_password)


@bp.route("/accounts/import/template.xlsx", methods=["GET"])
@permission_required("manage_players")
def import_template():
    conn = get_conn()
    wb = bulk_import.build_template_workbook(conn)
    conn.close()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="نموذج_استيراد_اللاعبين.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/accounts/import", methods=["POST"])
@permission_required("manage_players")
def import_players_execute():
    file = request.files.get("file")
    unified_password = request.form.get("unified_password", "").strip()
    if not file or not file.filename:
        flash("اختر ملف الإكسل أولًا")
        return redirect("/accounts/import")
    if len(unified_password) < 6:
        flash("كلمة المرور الموحدة يجب أن تكون 6 أحرف على الأقل")
        return redirect("/accounts/import")

    conn = get_conn()
    rows, errors = bulk_import.parse_workbook(conn, file.stream)
    if errors:
        conn.close()
        for e in errors[:25]:
            flash(f"⚠️ {e}")
        if len(errors) > 25:
            flash(f"...و{len(errors) - 25} خطأ إضافي. صحّح الملف وأعد رفعه — لم يتم إنشاء أي حساب.")
        else:
            flash("صحّح الأخطاء أعلاه وأعد رفع الملف — لم يتم إنشاء أي حساب.")
        return redirect("/accounts/import")

    created = bulk_import.import_players(conn, rows, unified_password, g.user["id"])
    conn.commit()
    conn.close()

    pdf_buf = build_credentials_pdf(created, unified_password)
    return send_file(pdf_buf, as_attachment=True, download_name="بيانات_دخول_اللاعبين.pdf", mimetype="application/pdf")


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
    # Served inline as plain text (not a forced download) — Render's free-tier
    # proxy was returning a spurious 503 to the browser for attachment
    # downloads on this route even though the app served 200 successfully.
    # Inline text can always be read/copy-saved reliably from the page itself.
    return Response(body, mimetype="text/plain; charset=utf-8")


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
