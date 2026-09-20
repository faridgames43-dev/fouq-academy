from flask import Blueprint, render_template, request, Response, g, abort, send_file
from datetime import date, timedelta
from db import get_conn, q, q1
from business.rbac import permission_required, branch_scope, login_required, parent_player_ids
from business import reports as rep
from business.dashboard import kpis
from business.pdf_export import build_generic_report_pdf, build_monthly_report_pdf, build_player_report_pdf

bp = Blueprint("reports_bp", __name__)


def _can_view_player_report(conn, player_id):
    """Real backend ownership check — not just a hidden button. Admin/coach
    roles keep their existing access; a PARENT may only see a report for a
    child actually linked to their account, and a PLAYER only their own."""
    role = g.user["role"]
    if role in ("SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"):
        return True
    if role == "PLAYER":
        player = q1(conn, "SELECT id FROM players WHERE id=? AND user_id=?", (player_id, g.user["id"]))
        return player is not None
    if role == "PARENT":
        return player_id in parent_player_ids(conn, g.user["id"])
    return False

REPORT_TYPES = [
    ("outstanding", "تقرير الحصص المستحقة"),
    ("attendance", "تقرير الحضور"),
    ("subscriptions", "تقرير الاشتراكات"),
    ("revenue", "تقرير الإيرادات"),
    ("coach_performance", "تقرير أداء المدربين"),
    ("monthly", "التقرير الشهري للإدارة"),
]


@bp.route("/reports")
@permission_required("view_reports")
def index():
    return render_template("reports_index.html", report_types=REPORT_TYPES)


@bp.route("/reports/<report_type>")
@permission_required("view_reports")
def view(report_type):
    conn = get_conn()
    branch_id = branch_scope(g.user)
    fmt = request.args.get("format")

    if report_type == "outstanding":
        rows = rep.outstanding_sessions_report(conn, branch_id)
        title = "تقرير الحصص المستحقة"
    elif report_type == "attendance":
        date_from = request.args.get("from") or (date.today() - timedelta(days=30)).isoformat()
        date_to = request.args.get("to") or date.today().isoformat()
        rows = rep.attendance_report(conn, date_from, date_to, branch_id)
        title = "تقرير الحضور"
    elif report_type == "subscriptions":
        rows = rep.subscriptions_report(conn, branch_id, request.args.get("status"))
        title = "تقرير الاشتراكات"
    elif report_type == "revenue":
        rows = rep.revenue_report(conn, branch_id)
        title = "تقرير الإيرادات"
    elif report_type == "coach_performance":
        rows = rep.coach_performance_report(conn, branch_id)
        title = "تقرير أداء المدربين"
    elif report_type == "monthly":
        conn.close()
        return monthly(fmt)
    else:
        conn.close()
        abort(404)

    conn.close()
    if fmt == "csv":
        csv_data = rep.to_csv(rows)
        return Response(csv_data, mimetype="text/csv",
                         headers={"Content-Disposition": f"attachment; filename={report_type}.csv"})
    if fmt == "pdf":
        buf = build_generic_report_pdf(title, rows)
        return send_file(buf, as_attachment=False, download_name=f"{report_type}.pdf", mimetype="application/pdf")
    return render_template("report_generic.html", rows=rows, title=title, report_type=report_type)


@bp.route("/reports/<report_type>/print")
@permission_required("view_reports")
def print_view(report_type):
    conn = get_conn()
    branch_id = branch_scope(g.user)
    if report_type == "outstanding":
        rows = rep.outstanding_sessions_report(conn, branch_id)
        title = "تقرير الحصص المستحقة"
    elif report_type == "subscriptions":
        rows = rep.subscriptions_report(conn, branch_id)
        title = "تقرير الاشتراكات"
    else:
        rows = q(conn, "SELECT 1")
        title = report_type
    conn.close()
    return render_template("print_generic_report.html", rows=rows, title=title)


@bp.route("/reports/monthly")
@permission_required("view_reports")
def monthly(fmt=None):
    conn = get_conn()
    branch_id = branch_scope(g.user)
    fmt = fmt or request.args.get("format")
    data = kpis(conn, branch_id)
    conn.close()
    if fmt == "pdf":
        buf = build_monthly_report_pdf(data)
        return send_file(buf, as_attachment=False, download_name="monthly_report.pdf", mimetype="application/pdf")
    return render_template("report_monthly.html", data=data, title="التقرير الشهري للإدارة")


@bp.route("/reports/monthly/print")
@permission_required("view_reports")
def monthly_print():
    conn = get_conn()
    branch_id = branch_scope(g.user)
    data = kpis(conn, branch_id)
    conn.close()
    return render_template("print_monthly_report.html", data=data)


@bp.route("/reports/player/<int:player_id>/pdf")
@login_required
def player_pdf(player_id):
    conn = get_conn()
    allowed = _can_view_player_report(conn, player_id)
    conn.close()
    if not allowed:
        abort(403)
    ctx = _player_report_context(player_id)
    if ctx is None:
        abort(404)
    buf = build_player_report_pdf(ctx)
    return send_file(buf, as_attachment=False, download_name=f"player_{player_id}_report.pdf", mimetype="application/pdf")


def _player_report_context(player_id):
    from business.entitlements import get_balances
    from business.assessments import player_development_timeline, child_label
    from business.levels import current_level
    from business.points import get_balance
    conn = get_conn()
    player = q1(conn, """SELECT p.*, c.name as category_name, gr.name as group_name, co.name as coach_name,
                         b.name as branch_name FROM players p LEFT JOIN categories c ON c.id=p.category_id
                         LEFT JOIN groups_ gr ON gr.id=p.group_id LEFT JOIN coaches co ON co.id=p.coach_id
                         LEFT JOIN branches b ON b.id=p.branch_id WHERE p.id=?""", (player_id,))
    if not player:
        conn.close()
        return None
    balances = get_balances(conn, player_id)
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (player_id,))["c"]
    present = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')", (player_id,))["c"]
    absent = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status='ABSENT'", (player_id,))["c"]
    attendance_pct = round(present / total_sessions * 100, 1) if total_sessions else 0
    dev = player_development_timeline(conn, player_id)
    level = current_level(conn, player_id)
    achievements = q(conn, """SELECT a.* FROM player_achievements pa JOIN achievements a ON a.id=pa.achievement_id
                              WHERE pa.player_id=?""", (player_id,))
    pts = get_balance(conn, player_id)
    conn.close()
    return {"player": player, "balances": balances, "attendance_pct": attendance_pct, "present": present,
            "absent": absent, "total_sessions": total_sessions, "dev": dev, "level": level,
            "achievements": achievements, "points": pts, "generated_at": date.today().isoformat()}


@bp.route("/reports/player/<int:player_id>/print")
@login_required
def player_print(player_id):
    from business.entitlements import get_balances
    from business.assessments import player_development_timeline, child_label
    from business.levels import current_level
    from business.points import get_balance
    conn = get_conn()
    if not _can_view_player_report(conn, player_id):
        conn.close()
        abort(403)
    player = q1(conn, """SELECT p.*, c.name as category_name, gr.name as group_name, co.name as coach_name,
                         b.name as branch_name FROM players p LEFT JOIN categories c ON c.id=p.category_id
                         LEFT JOIN groups_ gr ON gr.id=p.group_id LEFT JOIN coaches co ON co.id=p.coach_id
                         LEFT JOIN branches b ON b.id=p.branch_id WHERE p.id=?""", (player_id,))
    if not player:
        conn.close()
        abort(404)
    balances = get_balances(conn, player_id)
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (player_id,))["c"]
    present = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')", (player_id,))["c"]
    absent = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status='ABSENT'", (player_id,))["c"]
    attendance_pct = round(present / total_sessions * 100, 1) if total_sessions else 0
    dev = player_development_timeline(conn, player_id)
    level = current_level(conn, player_id)
    achievements = q(conn, """SELECT a.* FROM player_achievements pa JOIN achievements a ON a.id=pa.achievement_id
                              WHERE pa.player_id=?""", (player_id,))
    notes = q1(conn, "SELECT notes FROM players WHERE id=?", (player_id,))
    pts = get_balance(conn, player_id)
    conn.close()
    first = dev[0]["overall"] if dev else None
    last = dev[-1]["overall"] if dev else None
    return render_template("print_player_report.html", player=player, balances=balances, attendance_pct=attendance_pct,
                            present=present, absent=absent, total_sessions=total_sessions, dev=dev, level=level,
                            achievements=achievements, first_overall=first, last_overall=last, points=pts,
                            child_label=child_label, generated_at=date.today().isoformat())
