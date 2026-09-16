from flask import Blueprint, render_template, g, redirect
from db import get_conn
from business.rbac import login_required
from business.notifications import list_for_user, mark_read, run_daily_checks, list_recent

bp = Blueprint("notifications_bp", __name__)


@bp.route("/notifications")
@login_required
def index():
    conn = get_conn()
    if g.user["role"] in ("SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"):
        run_daily_checks(conn)
        conn.commit()
        items = list_recent(conn, 50)
    else:
        items = list_for_user(conn, g.user["id"], 50)
    for i in items:
        if not i["is_read"]:
            mark_read(conn, i["id"])
    conn.commit()
    conn.close()
    return render_template("notifications.html", items=items)
