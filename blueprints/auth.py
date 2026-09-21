from flask import Blueprint, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import check_password_hash
from db import get_conn, q1
from business.accounts import change_own_password, AccountError

bp = Blueprint("auth", __name__)

ROLE_HOME = {
    "SUPER_ADMIN": "/dashboard", "PROJECT_MANAGER": "/dashboard", "BRANCH_MANAGER": "/dashboard",
    "SUPERVISOR": "/dashboard", "COACH": "/coach", "PARENT": "/parent", "PLAYER": "/me",
}

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        conn = get_conn()
        user = q1(conn, "SELECT * FROM users WHERE email=? OR phone=?", (identifier, identifier))
        if not user:
            # players log in with their FOUQ player code instead of an email/phone
            player = q1(conn, "SELECT user_id FROM players WHERE player_code=? AND user_id IS NOT NULL",
                        (identifier,))
            if player:
                user = q1(conn, "SELECT * FROM users WHERE id=?", (player["user_id"],))
        conn.close()
        if user and check_password_hash(user["password_hash"], password) and user["active"]:
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(ROLE_HOME.get(user["role"], "/dashboard"))
        flash_error = "بيانات الدخول غير صحيحة"
        return render_template("login.html", error=flash_error)
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        conn = get_conn()
        try:
            if request.form.get("new_password", "") != request.form.get("confirm_password", ""):
                raise AccountError("كلمتا المرور الجديدتان غير متطابقتين")
            change_own_password(
                conn, session["user_id"],
                request.form.get("current_password", ""),
                request.form.get("new_password", ""),
            )
            conn.commit()
            conn.close()
            flash("تم تغيير كلمة المرور بنجاح")
            return redirect(ROLE_HOME.get(session.get("role"), "/dashboard"))
        except AccountError as e:
            conn.close()
            flash(str(e))
    forced = bool(g.get("user") and g.user.get("must_reset_password"))
    return render_template("change_password.html", forced=forced)


@bp.route("/")
def root():
    if session.get("user_id"):
        return redirect(ROLE_HOME.get(session.get("role"), "/dashboard"))
    return redirect(url_for("auth.login"))
