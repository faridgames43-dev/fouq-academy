from flask import Blueprint, render_template, g
from db import get_conn, q
from business.rbac import permission_required, branch_scope

bp = Blueprint("retention_bp", __name__)


@bp.route("/retention")
@permission_required("manage_crm")
def index():
    conn = get_conn()
    branch_id = branch_scope(g.user)
    bc = " AND p.branch_id=?" if branch_id else ""
    params = (branch_id,) if branch_id else ()

    not_attended_14d = q(conn, f"""SELECT p.* FROM players p WHERE p.status='ACTIVE' {bc}
        AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.player_id=p.id AND a.status IN ('PRESENT','LATE')
                         AND a.checked_at >= datetime('now','-14 day'))""", params)

    declining = q(conn, f"""SELECT p.*,
        (SELECT ROUND(100.0*SUM(CASE WHEN a2.status IN ('PRESENT','LATE') THEN 1 ELSE 0 END)/COUNT(*),1) FROM attendance a2
           JOIN training_sessions t2 ON t2.id=a2.training_session_id WHERE a2.player_id=p.id AND t2.session_date>=date('now','-14 day')) as recent_rate
        FROM players p WHERE p.status='ACTIVE' {bc}""", params)
    declining = [d for d in declining if d["recent_rate"] is not None and d["recent_rate"] < 50]

    expiring = q(conn, f"""SELECT p.*, s.end_date FROM players p JOIN subscriptions s ON s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=p.id)
        WHERE s.status='EXPIRING_SOON' {bc}""", params)

    low_sessions = q(conn, f"""SELECT p.*, COALESCE(SUM(se.quantity_remaining),0) as remaining FROM players p
        LEFT JOIN session_entitlements se ON se.player_id=p.id AND se.status='ACTIVE'
        WHERE p.status='ACTIVE' {bc} GROUP BY p.id HAVING remaining <= 2""", params)

    unused_compensation = q(conn, f"""SELECT p.*, SUM(se.quantity_remaining) as comp_remaining FROM players p
        JOIN session_entitlements se ON se.player_id=p.id WHERE se.type IN ('COMPENSATION','BONUS')
        AND se.status='ACTIVE' AND se.quantity_remaining > 0 {bc} GROUP BY p.id""", params)

    not_assessed = q(conn, f"""SELECT p.* FROM players p WHERE p.status='ACTIVE' {bc}
        AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.player_id=p.id)""", params)

    not_renewed = q(conn, f"""SELECT p.*, s.end_date FROM players p JOIN subscriptions s ON s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=p.id)
        WHERE s.status='EXPIRED' {bc}""", params)

    conn.close()
    return render_template("retention.html", not_attended_14d=not_attended_14d, declining=declining,
                            expiring=expiring, low_sessions=low_sessions, unused_compensation=unused_compensation,
                            not_assessed=not_assessed, not_renewed=not_renewed)
