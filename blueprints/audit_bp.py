from flask import Blueprint, render_template, request
from db import get_conn, q
from business.rbac import permission_required

bp = Blueprint("audit_bp", __name__)


@bp.route("/audit-log")
@permission_required("view_audit_log")
def index():
    conn = get_conn()
    entity_filter = request.args.get("entity")
    sql = """SELECT al.*, u.name as user_name FROM audit_logs al LEFT JOIN users u ON u.id=al.user_id WHERE 1=1"""
    params = []
    if entity_filter:
        sql += " AND al.entity_type=?"
        params.append(entity_filter)
    sql += " ORDER BY al.id DESC LIMIT 300"
    logs = q(conn, sql, tuple(params))
    entities = q(conn, "SELECT DISTINCT entity_type FROM audit_logs")
    conn.close()
    return render_template("audit_log.html", logs=logs, entities=entities, entity_filter=entity_filter)
