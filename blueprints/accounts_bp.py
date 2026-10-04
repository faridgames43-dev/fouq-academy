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
from business.pdf_export import build_credentials_pdf, build_login_guide_pdf, LOGIN_GUIDE_STEPS, build_full_roster_pdf, build_credentials_table_pdf
import json
import io
import os

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


@bp.route("/accounts/login-guide.pdf", methods=["GET"])
@permission_required("manage_players")
def login_guide_pdf():
    """Generic, reusable 'how to log in' walkthrough (no player names or
    real secrets — screenshots of a disposable demo account) — a separate,
    standalone handout so it never needs regenerating per-import; can be
    downloaded any time and re-printed alongside any batch of credential
    cards."""
    base = os.path.join("static", "img", "login_guide")
    paths = [os.path.join(base, f"{i:02d}.png") for i in range(1, len(LOGIN_GUIDE_STEPS) + 1)]
    buf = build_login_guide_pdf(paths)
    return send_file(buf, as_attachment=True, download_name="دليل_تسجيل_الدخول.pdf", mimetype="application/pdf")


ROSTER_TYPE_FILTERS = {"fouq": "AND p.player_type = 'FOUQ'", "legacy": "AND p.player_type = 'LEGACY'", "all": ""}
ROSTER_TYPE_LABELS = {"fouq": "لاعبو أكاديمية فوق فقط", "legacy": "لاعبو نادي تواصل فقط", "all": "جميع اللاعبين"}
ROSTER_TYPE_FILENAMES = {"fouq": "بيانات_لاعبي_أكاديمية_فوق.pdf", "legacy": "بيانات_لاعبي_نادي_تواصل.pdf",
                         "all": "بيانات_جميع_اللاعبين.pdf"}


def _roster_type():
    t = (request.args.get("type") or "all").lower()
    return t if t in ROSTER_TYPE_FILTERS else "all"


@bp.route("/accounts/export-fouq-roster", methods=["GET"])
@permission_required("manage_players")
def export_fouq_roster_preview():
    """معاينة قبل التحميل مع اختيار نوع التصدير: الكل / أكاديمية فوق فقط /
    نادي تواصل فقط. كلمات المرور لا تظهر هنا ولا في الملف الأساسي لأنها
    مخزّنة بتشفير أحادي الاتجاه ولا يمكن استرجاعها."""
    rtype = _roster_type()
    conn = get_conn()
    players = q(conn, f"""
        SELECT p.id, p.first_name, p.last_name, p.player_code AS code, p.status, p.player_type,
               b.name AS branch_name, c.name AS category_name
        FROM players p
        LEFT JOIN branches b ON b.id = p.branch_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE 1=1 {ROSTER_TYPE_FILTERS[rtype]}
        ORDER BY p.first_name, p.last_name
    """)
    counts = {
        "all": q1(conn, "SELECT COUNT(*) c FROM players")["c"],
        "fouq": q1(conn, "SELECT COUNT(*) c FROM players WHERE player_type='FOUQ'")["c"],
        "legacy": q1(conn, "SELECT COUNT(*) c FROM players WHERE player_type='LEGACY'")["c"],
    }
    conn.close()
    return render_template("export_fouq_roster_preview.html", players=players, count=len(players),
                            rtype=rtype, type_labels=ROSTER_TYPE_LABELS, counts=counts)


@bp.route("/accounts/export-fouq-roster.pdf", methods=["GET"])
@permission_required("manage_players")
def export_fouq_roster_pdf():
    rtype = _roster_type()
    conn = get_conn()
    players = q(conn, f"""
        SELECT p.first_name, p.last_name, p.player_code AS code
        FROM players p
        WHERE 1=1 {ROSTER_TYPE_FILTERS[rtype]}
        ORDER BY p.first_name, p.last_name
    """)
    conn.close()
    buf = build_full_roster_pdf([dict(p) for p in players], roster_type=rtype)
    return send_file(buf, as_attachment=True, download_name=ROSTER_TYPE_FILENAMES[rtype],
                      mimetype="application/pdf")


@bp.route("/accounts/credentials-sheet", methods=["GET"])
@permission_required("manage_players")
def credentials_sheet_form():
    """ملف PDF بأسماء اللاعبين (أبجديًا) + اسم المستخدم + كلمة المرور الموحدة."""
    conn = get_conn()
    counts = {
        "all": q1(conn, "SELECT COUNT(*) c FROM players")["c"],
        "FOUQ": q1(conn, "SELECT COUNT(*) c FROM players WHERE player_type='FOUQ'")["c"],
        "LEGACY": q1(conn, "SELECT COUNT(*) c FROM players WHERE player_type='LEGACY'")["c"],
    }
    categories = q(conn, "SELECT id, name FROM categories ORDER BY name")
    conn.close()
    return render_template("credentials_sheet.html", counts=counts, categories=categories,
                            suggested_password=generate_password())


@bp.route("/accounts/credentials-sheet.pdf", methods=["POST"])
@permission_required("manage_players")
def credentials_sheet_pdf():
    # POST (لا GET) حتى لا تظهر كلمة المرور في رابط الصفحة أو سجل المتصفح
    pw = (request.form.get("unified_password") or "").strip()
    scope = request.form.get("scope", "all")
    category_id = request.form.get("category_id") or None
    style = request.form.get("style", "table")
    if len(pw) < 4:
        flash("اكتب كلمة المرور الموحدة (4 أحرف على الأقل)")
        return redirect("/accounts/credentials-sheet")
    sql = "SELECT first_name, last_name, player_code AS code, user_id FROM players WHERE 1=1"
    params = []
    if scope in ("FOUQ", "LEGACY"):
        sql += " AND player_type=?"; params.append(scope)
    if category_id:
        sql += " AND category_id=?"; params.append(category_id)
    conn = get_conn()
    players = [dict(r) for r in q(conn, sql + " ORDER BY first_name, last_name", tuple(params))]
    if not players:
        conn.close()
        flash("لا يوجد لاعبون مطابقون")
        return redirect("/accounts/credentials-sheet")
    if request.form.get("apply") == "1":
        # اعتماد كلمة المرور فعليًا (لمن نسي كلمة المرور الموحدة): تُستبدل لحسابات
        # اللاعبين المطابقين فقط، وتُجبر على التغيير عند أول دخول.
        h = generate_password_hash(pw)
        uids = [p["user_id"] for p in players if p.get("user_id")]
        for uid in uids:
            ex(conn, "UPDATE users SET password_hash=?, must_reset_password=1 WHERE id=? AND role='PLAYER'", (h, uid))
        audit_log(conn, g.user["id"], "SET_UNIFIED_PASSWORD", "users", None,
                  after={"accounts": len(uids), "players_in_scope": len(players)},
                  reason="اعتماد كلمة مرور موحدة جديدة لحسابات اللاعبين")
        conn.commit()
    conn.close()
    players = [{k: v for k, v in p.items() if k != "user_id"} for p in players]
    buf = build_credentials_table_pdf(players, pw) if style == "table" else build_credentials_pdf(players, pw)
    return send_file(buf, as_attachment=True, download_name="بيانات_دخول_اللاعبين.pdf", mimetype="application/pdf")


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
