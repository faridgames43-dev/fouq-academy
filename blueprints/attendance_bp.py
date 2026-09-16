from flask import Blueprint, render_template, request, redirect, g, flash, jsonify, abort
from datetime import date
from db import get_conn, q, q1, ex
from business.rbac import login_required, permission_required, coach_group_ids, branch_scope
from business import attendance as att
from business import achievements as ach
from business.audit import log as audit_log

bp = Blueprint("attendance_bp", __name__)


def _scope_sql(base_sql, params):
    if g.user["role"] == "COACH":
        conn = get_conn()
        gids = coach_group_ids(conn, g.user["id"])
        conn.close()
        if not gids:
            return base_sql + " AND 1=0", params
        placeholders = ",".join(["?"] * len(gids))
        return base_sql + f" AND ts.group_id IN ({placeholders})", params + gids
    return base_sql, params


@bp.route("/attendance")
@login_required
def today():
    conn = get_conn()
    sel_date = request.args.get("date", date.today().isoformat())
    sql = """SELECT ts.*, g.name as group_name, c.name as coach_name,
             (SELECT COUNT(*) FROM attendance a WHERE a.training_session_id=ts.id AND a.status IN ('PRESENT','LATE')) as present_count,
             (SELECT COUNT(*) FROM players p WHERE p.group_id=ts.group_id) as roster_size
             FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id
             LEFT JOIN coaches c ON c.id=ts.coach_id WHERE ts.session_date=?"""
    params = [sel_date]
    sql, params = _scope_sql(sql, params)
    sessions = q(conn, sql, tuple(params))
    conn.close()
    return render_template("attendance_today.html", sessions=sessions, sel_date=sel_date)


@bp.route("/attendance/session/<int:session_id>")
@login_required
def session_detail(session_id):
    conn = get_conn()
    ts = q1(conn, "SELECT ts.*, g.name as group_name FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id WHERE ts.id=?",
            (session_id,))
    if not ts:
        abort(404)
    roster = q(conn, "SELECT * FROM players WHERE group_id=? ORDER BY first_name", (ts["group_id"],))
    existing = {a["player_id"]: a for a in att.get_attendance_for_session(conn, session_id)}
    for p in roster:
        a = existing.get(p["id"])
        p["attendance_status"] = a["status"] if a else None
        p["attendance_id"] = a["id"] if a else None
    conn.close()
    can_override = g.user["role"] in ("SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR")
    return render_template("attendance_session.html", ts=ts, roster=roster, can_override=can_override)


@bp.route("/attendance/session/<int:session_id>/mark", methods=["POST"])
@permission_required("take_attendance")
def mark(session_id):
    conn = get_conn()
    data = request.get_json(force=True)
    try:
        result = att.mark_attendance(conn, session_id, data["player_id"], data["status"], g.user["id"],
                                      allow_override=data.get("override", False))
        ach.check_after_attendance(conn, data["player_id"], g.user["id"])
        conn.commit()
        conn.close()
        return jsonify({"ok": True, **result})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


@bp.route("/attendance/session/<int:session_id>/scan", methods=["POST"])
@permission_required("take_attendance")
def scan(session_id):
    conn = get_conn()
    code = request.get_json(force=True).get("code", "")
    try:
        result = att.checkin_by_code(conn, session_id, code, g.user["id"])
        ach.check_after_attendance(conn, result["player"]["id"], g.user["id"])
        conn.commit()
        player = result["player"]
        conn.close()
        return jsonify({"ok": True, "already": result["already"], "message": result["message"],
                         "player_name": f"{player['first_name']} {player['last_name']}",
                         "remaining_total": result["remaining_total"], "player_id": player["id"]})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


@bp.route("/attendance/session/<int:session_id>/mark_all_present", methods=["POST"])
@permission_required("take_attendance")
def mark_all_present(session_id):
    conn = get_conn()
    ts = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (session_id,))
    roster = q(conn, "SELECT id FROM players WHERE group_id=?", (ts["group_id"],))
    results = att.mark_all_present(conn, session_id, [p["id"] for p in roster], g.user["id"])
    conn.commit()
    conn.close()
    ok_count = sum(1 for r in results if r["ok"])
    flash(f"تم تحضير {ok_count} لاعب. راجع الحالات التي لم تنجح إن وجدت.")
    return redirect(f"/attendance/session/{session_id}")


@bp.route("/attendance/<int:attendance_id>/cancel", methods=["POST"])
@permission_required("cancel_attendance")
def cancel(attendance_id):
    conn = get_conn()
    row = q1(conn, "SELECT * FROM attendance WHERE id=?", (attendance_id,))
    try:
        att.cancel_attendance(conn, attendance_id, g.user["id"], request.form.get("reason", "إلغاء تحضير"))
        conn.commit()
        flash("تم إلغاء التحضير وعكس أي خصم مرتبط به")
    except att.AttendanceError as e:
        flash(str(e))
    session_id = row["training_session_id"] if row else None
    conn.close()
    return redirect(f"/attendance/session/{session_id}" if session_id else "/attendance")


@bp.route("/attendance/session/<int:session_id>/complete", methods=["POST"])
@permission_required("take_attendance")
def complete(session_id):
    conn = get_conn()
    ts = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (session_id,))
    roster_count = q1(conn, "SELECT COUNT(*) c FROM players WHERE group_id=?", (ts["group_id"],))["c"]
    marked_count = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE training_session_id=?", (session_id,))["c"]
    if marked_count < roster_count:
        flash(f"تنبيه: لم يتم تحضير {roster_count - marked_count} لاعب من القائمة. أكمل التحضير أولًا أو أكّد التجاوز.")
        if request.form.get("force") != "1":
            conn.close()
            return redirect(f"/attendance/session/{session_id}")
    ex(conn, "UPDATE training_sessions SET status='COMPLETED' WHERE id=?", (session_id,))
    audit_log(conn, g.user["id"], "COMPLETE_SESSION", "training_sessions", session_id)
    conn.commit()
    conn.close()
    flash("تم إغلاق الحصة بنجاح ✅")
    return redirect("/attendance")


@bp.route("/attendance/new", methods=["GET", "POST"])
@permission_required("manage_players")
def new_session():
    conn = get_conn()
    if request.method == "POST":
        f = request.form
        group = q1(conn, "SELECT * FROM groups_ WHERE id=?", (f.get("group_id"),))
        sid = ex(conn, """INSERT INTO training_sessions(branch_id, group_id, coach_id, facility_id, session_date,
                          start_time, end_time, goal) VALUES (?,?,?,?,?,?,?,?)""",
                 (group["branch_id"], group["id"], group["coach_id"], f.get("facility_id") or None,
                  f.get("session_date"), f.get("start_time"), f.get("end_time"), f.get("goal")))
        conn.commit(); conn.close()
        flash("تم إنشاء الحصة")
        return redirect("/attendance?date=" + f.get("session_date"))
    groups = q(conn, "SELECT * FROM groups_")
    facilities = q(conn, "SELECT * FROM facilities")
    conn.close()
    return render_template("session_form.html", groups=groups, facilities=facilities)
