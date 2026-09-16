from flask import Blueprint, render_template, request, redirect, g, flash
from db import get_conn, q, q1, ex
from business.rbac import permission_required, branch_scope
from business.subscriptions import sync_subscription_statuses
from business.entitlements import get_balances, sync_expirations
from business.renewal_risk import compute_risk

bp = Blueprint("renewals_bp", __name__)


@bp.route("/renewals")
@permission_required("manage_subscriptions")
def board():
    conn = get_conn()
    sync_subscription_statuses(conn)
    sync_expirations(conn)
    branch_id = branch_scope(g.user)
    bc = " AND p.branch_id=?" if branch_id else ""
    params = (branch_id,) if branch_id else ()

    def bucket(sql_extra, extra_params=()):
        rows = q(conn, f"""SELECT p.*, s.status as sub_status, s.end_date FROM players p
                 JOIN subscriptions s ON s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=p.id)
                 WHERE 1=1 {bc} {sql_extra}""", params + extra_params)
        for r in rows:
            r["balances"] = get_balances(conn, r["id"])
            r["risk"] = compute_risk(conn, r["id"])
        return rows

    expiring_7 = bucket("AND s.status='EXPIRING_SOON'")
    expired_with_comp = [r for r in bucket("AND s.status='EXPIRED'") if r["balances"]["TOTAL"] > 0]
    expired_no_sessions = [r for r in bucket("AND s.status='EXPIRED'") if r["balances"]["TOTAL"] <= 0]
    active = bucket("AND s.status='ACTIVE'")

    conn.close()
    return render_template("renewals_board.html", expiring_7=expiring_7, expired_with_comp=expired_with_comp,
                            expired_no_sessions=expired_no_sessions, active=active)


@bp.route("/players/<int:player_id>/renewal_note", methods=["POST"])
@permission_required("manage_subscriptions")
def add_note(player_id):
    conn = get_conn()
    f = request.form
    ex(conn, """INSERT INTO renewal_notes(player_id, contacted_by, note, stage, next_follow_up, outcome)
                VALUES (?,?,?,?,?,?)""",
       (player_id, g.user["id"], f.get("note"), f.get("stage", "CONTACTED"), f.get("next_follow_up"),
        f.get("outcome") or None))
    conn.commit()
    conn.close()
    flash("تم تسجيل المتابعة")
    return redirect(request.referrer or "/renewals")
