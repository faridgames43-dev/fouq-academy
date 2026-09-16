from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from db import get_conn, q1

bp = Blueprint("auth", __name__)

ROLE_HOME = {
    "SUPER_ADMIN": "/dashboard", "PROJECT_MANAGER": "/dashboard", "BRANCH_MANAGER": "/dashboard",
    "SUPERVISOR": "/dashboard", "COACH": "/coach", "PARENT": "/parent", "PLAYER": "/me",
}

DEMO_ACCOUNTS = [
    ("مدير المشروع (يرى كل شيء عمليًا)", "pm@fouq.sa", "PROJECT_MANAGER"),
    ("الإدارة العليا", "superadmin@fouq.sa", "SUPER_ADMIN"),
    ("مدير الفرع", "branchmgr@fouq.sa", "BRANCH_MANAGER"),
    ("المشرف التشغيلي", "supervisor@fouq.sa", "SUPERVISOR"),
    ("مدرب - الكابتن سعد", "coach1@fouq.sa", "COACH"),
    ("مدرب - الكابتن فهد", "coach2@fouq.sa", "COACH"),
]


def _sample_contacts():
    conn = get_conn()
    parent_user = q1(conn, """SELECT u.name, u.phone FROM users u JOIN parents p ON p.user_id=u.id
                              WHERE p.name='محمد الأحمدي'""")
    player_user = q1(conn, """SELECT u.name, u.phone FROM users u JOIN players p ON p.user_id=u.id
                              WHERE p.player_code='FOUQ-0001'""")
    conn.close()
    return parent_user, player_user


@bp.route("/login", methods=["GET", "POST"])
def login():
    parent_user, player_user = _sample_contacts()
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        conn = get_conn()
        user = q1(conn, "SELECT * FROM users WHERE email=? OR phone=?", (identifier, identifier))
        conn.close()
        if user and check_password_hash(user["password_hash"], password) and user["active"]:
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(ROLE_HOME.get(user["role"], "/dashboard"))
        flash_error = "بيانات الدخول غير صحيحة"
        return render_template("login.html", error=flash_error, demo_accounts=DEMO_ACCOUNTS,
                                parent_user=parent_user, player_user=player_user)
    return render_template("login.html", demo_accounts=DEMO_ACCOUNTS, parent_user=parent_user, player_user=player_user)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/")
def root():
    if session.get("user_id"):
        return redirect(ROLE_HOME.get(session.get("role"), "/dashboard"))
    return redirect(url_for("auth.login"))
