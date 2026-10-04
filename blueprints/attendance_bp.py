from flask import Blueprint, render_template, request, redirect, g, flash, jsonify, abort
from datetime import date
from db import get_conn, q, q1, ex
from business.rbac import login_required, permission_required, coach_group_ids, branch_scope
from business import attendance as att
from business import achievements as ach
from business import challenges as chal
from business import tv_checkin as tv
from business.audit import log as audit_log
from business.points import get_balance as points_balance, ADD_REASONS, SUBTRACT_REASONS, PointsError
from business.points import award_points
from business.levels import current_level

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


@bp.route("/attendance/no-balance")
@permission_required("manage_compensation")
def no_balance_report():
    """لاعبون حضروا بدون رصيد حصص مسجّل — للمراجعة وإضافة/تجديد الرصيد."""
    conn = get_conn()
    rows = q(conn, """SELECT p.id, p.first_name, p.last_name, p.player_code, COUNT(*) AS cnt, MAX(ts.session_date) AS last_date
                      FROM attendance a JOIN players p ON p.id=a.player_id
                      JOIN training_sessions ts ON ts.id=a.training_session_id
                      WHERE a.no_balance=1 AND a.status IN ('PRESENT','LATE')
                      GROUP BY p.id ORDER BY cnt DESC, p.first_name""")
    from business.entitlements import get_balances
    for r in rows:
        r["balance"] = get_balances(conn, r["id"])["TOTAL"]
    conn.close()
    return render_template("attendance_no_balance.html", rows=rows)


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
        p["no_balance"] = bool(a and a.get("no_balance"))
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
        chal.check_and_complete(conn, data["player_id"], g.user["id"])
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
        chal.check_and_complete(conn, result["player"]["id"], g.user["id"])
        conn.commit()
        player = result["player"]
        pts = points_balance(conn, player["id"])
        level = current_level(conn, player["id"])
        conn.close()
        return jsonify({"ok": True, "already": result["already"], "message": result["message"],
                         "player_name": f"{player['first_name']} {player['last_name']}",
                         "player_code": player["player_code"], "photo_url": player.get("photo_url"),
                         "points": pts, "level_name": level["name"] if level else "الانطلاقة",
                         "remaining_total": result["remaining_total"], "player_id": player["id"]})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


@bp.route("/attendance/session/<int:session_id>/tv")
@permission_required("take_attendance")
def tv_screen(session_id):
    conn = get_conn()
    ts = q1(conn, "SELECT ts.*, g.name as group_name FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id WHERE ts.id=?",
            (session_id,))
    conn.close()
    if not ts:
        abort(404)
    return render_template("attendance_tv.html", ts=ts)


@bp.route("/attendance/session/<int:session_id>/tv/state")
@permission_required("take_attendance")
def tv_state(session_id):
    conn = get_conn()
    state = tv.roster_state(conn, session_id)
    conn.commit()  # current_level() may lazily create the first level row
    conn.close()
    if not state:
        return jsonify({"ok": False, "message": "الحصة غير موجودة"}), 404
    return jsonify(state)


@bp.route("/attendance/session/<int:session_id>/tv/start", methods=["POST"])
@permission_required("take_attendance")
def tv_start(session_id):
    conn = get_conn()
    try:
        tv.start_checkin(conn, session_id, g.user["id"])
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


@bp.route("/attendance/session/<int:session_id>/tv/close", methods=["POST"])
@permission_required("take_attendance")
def tv_close(session_id):
    conn = get_conn()
    try:
        result = tv.close_checkin(conn, session_id, g.user["id"], mark_absent=True)
        conn.commit()
        conn.close()
        return jsonify({"ok": True, **result})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


@bp.route("/attendance/session/<int:session_id>/tv/scan", methods=["POST"])
@permission_required("take_attendance")
def tv_scan(session_id):
    conn = get_conn()
    data = request.get_json(force=True)
    try:
        result = tv.checkin_player(conn, session_id, g.user["id"], code=data.get("code"),
                                    player_id=data.get("player_id"))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, **result})
    except att.AttendanceError as e:
        conn.rollback(); conn.close()
        return jsonify({"ok": False, "message": str(e)}), 400


QUICK_ADD_DEFAULTS = {"الحضور المبكر": 10, "الانضباط": 10, "الروح الرياضية": 10, "مساعدة زميل": 10,
                      "الفوز بتحدٍ": 15, "تطور ملحوظ": 15, "لاعب الحصة": 10, "الالتزام باللباس": 5}
QUICK_SUB_DEFAULTS = {"التأخر": 5, "سوء السلوك": 10, "السب": 15, "عدم الالتزام": 10,
                      "إفساد التدريب": 10, "مخالفة تعليمات المدرب": 10}


@bp.route("/attendance/session/<int:session_id>/quick")
@permission_required("take_attendance")
def quick_entry(session_id):
    """رصد سريع: اختر لاعبًا (أو عدة لاعبين) ← اضغط سببًا جاهزًا ← تُرصد النقاط فورًا،
    مع خانة ملاحظة اختيارية. كله في شاشة واحدة بدون تنقل بين صفحات."""
    conn = get_conn()
    ts = q1(conn, "SELECT ts.*, g.name as group_name FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id WHERE ts.id=?",
            (session_id,))
    if not ts:
        conn.close()
        abort(404)
    roster = q(conn, "SELECT id, first_name, last_name, photo_url, player_code FROM players WHERE group_id=? ORDER BY first_name, last_name",
               (ts["group_id"],))
    for p in roster:
        p["points"] = points_balance(conn, p["id"])
    conn.commit()
    conn.close()
    return render_template("attendance_quick.html", ts=ts, roster=roster,
                            add_reasons=[(r, QUICK_ADD_DEFAULTS.get(r, 10)) for r, _ in ADD_REASONS],
                            sub_reasons=[(r, QUICK_SUB_DEFAULTS.get(r, 5)) for r, _ in SUBTRACT_REASONS])


@bp.route("/attendance/session/<int:session_id>/quick/save", methods=["POST"])
@permission_required("take_attendance")
def quick_save(session_id):
    from blueprints.points_bp import _apply_points
    conn = get_conn()
    data = request.get_json(force=True)
    ids = [int(x) for x in data.get("player_ids", [])]
    reason = (data.get("reason") or "").strip()
    note = (data.get("note") or "").strip()
    try:
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if not ids:
        conn.close()
        return jsonify({"ok": False, "message": "حدد لاعبًا واحدًا على الأقل"}), 400
    if not amount and not note:
        conn.close()
        return jsonify({"ok": False, "message": "اختر سببًا للنقاط أو اكتب ملاحظة"}), 400
    cat_map = dict(ADD_REASONS); cat_map.update(dict(SUBTRACT_REASONS))
    results = []
    for pid in ids:
        entry = {"player_id": pid, "ok": True}
        try:
            if amount:
                _apply_points(conn, pid, amount, reason or "رصد سريع", cat_map.get(reason, "ADMIN"), note or None)
            if note:
                ex(conn, "INSERT INTO player_notes(player_id, training_session_id, note, created_by) VALUES (?,?,?,?)",
                   (pid, session_id, note, g.user["id"]))
                audit_log(conn, g.user["id"], "PLAYER_NOTE", "players", pid, after={"note": note})
            entry["points"] = points_balance(conn, pid)
        except PointsError as e:
            entry = {"player_id": pid, "ok": False, "message": str(e)}
        results.append(entry)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "saved": sum(1 for r in results if r["ok"]), "results": results})


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
        if row:
            ach.recheck_after_attendance_cancel(conn, row["player_id"], g.user["id"])
        conn.commit()
        flash("تم إلغاء التحضير وعكس أي خصم مرتبط به")
    except att.AttendanceError as e:
        flash(str(e))
    session_id = row["training_session_id"] if row else None
    conn.close()
    return redirect(f"/attendance/session/{session_id}" if session_id else "/attendance")


@bp.route("/attendance/session/<int:session_id>/bulk_mark", methods=["POST"])
@permission_required("take_attendance")
def bulk_mark(session_id):
    """🟨 تعديل التحضير دفعة واحدة: تحديد فردي/جماعي وتغيير الحالة لكل
    المحددين بضغطة واحدة — يعيد استخدام نفس دالة mark_attendance المُختبرة
    لكل لاعب، فتبقى كل قواعد الحصص/الاستحقاق سارية تمامًا كما في التحضير الفردي."""
    conn = get_conn()
    data = request.get_json(force=True)
    player_ids = data.get("player_ids", [])
    status = data.get("status")
    if status not in ("PRESENT", "LATE", "ABSENT", "EXCUSED"):
        conn.close()
        return jsonify({"ok": False, "message": "حالة غير صالحة"}), 400
    results = []
    for pid in player_ids:
        try:
            r = att.mark_attendance(conn, session_id, int(pid), status, g.user["id"])
            ach.check_after_attendance(conn, int(pid), g.user["id"])
            chal.check_and_complete(conn, int(pid), g.user["id"])
            results.append({"player_id": int(pid), "ok": True, **r})
        except att.AttendanceError as e:
            results.append({"player_id": int(pid), "ok": False, "message": str(e)})
    conn.commit()
    conn.close()
    ok_count = sum(1 for r in results if r["ok"])
    return jsonify({"ok": True, "updated": ok_count, "total": len(player_ids), "status": status, "results": results})


@bp.route("/attendance/session/<int:session_id>/bulk_cancel", methods=["POST"])
@permission_required("cancel_attendance")
def bulk_cancel(session_id):
    """🟥 إلغاء تحضير محددين / إلغاء الجميع — يعكس تلقائيًا أي خصم حصة
    مرتبط بكل سجل (نفس منطق الإلغاء الفردي المُختبر)، ثم يراجع أهلية أي
    إنجاز مرتبط بعدد الحضور (مثل أول حصة / 10 حصص) ويعكس نقاطه عبر سجل
    عكسي دائمًا — لا يُحذف أي سجل نقاط نهائيًا."""
    conn = get_conn()
    data = request.get_json(force=True)
    cancel_all = bool(data.get("all"))
    player_ids = set(int(x) for x in data.get("player_ids", []) or [])
    rows = q(conn, "SELECT * FROM attendance WHERE training_session_id=?", (session_id,))
    targets = rows if cancel_all else [r for r in rows if r["player_id"] in player_ids]
    cancelled = 0
    for row in targets:
        try:
            att.cancel_attendance(conn, row["id"], g.user["id"], "إلغاء جماعي")
            ach.recheck_after_attendance_cancel(conn, row["player_id"], g.user["id"])
            cancelled += 1
        except att.AttendanceError:
            pass
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "cancelled": cancelled, "total": len(targets)})


@bp.route("/attendance/session/<int:session_id>/delete", methods=["POST"])
@permission_required("cancel_attendance")
def delete_session(session_id):
    """🗑️ حذف حصة تحضير: يعكس خصم الحصص والنقاط لكل من حضر ثم يحذف الحصة."""
    conn = get_conn()
    ts = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (session_id,))
    if not ts:
        conn.close()
        flash("الحصة غير موجودة")
        return redirect("/attendance")
    try:
        res = att.delete_training_session(conn, session_id, g.user["id"])
        for pid in res["player_ids"]:
            ach.recheck_after_attendance_cancel(conn, pid, g.user["id"])
        conn.commit()
        flash(f"تم حذف الحصة نهائيًا" + (f" وإلغاء {res['cancelled']} سجل تحضير مع عكس الخصم والنقاط" if res["cancelled"] else ""))
    except att.AttendanceError as e:
        conn.rollback()
        flash(str(e))
    conn.close()
    return redirect(f"/attendance?date={ts['session_date']}")


@bp.route("/attendance/tv")
@permission_required("take_attendance")
def tv_launcher():
    """مدخل شاشة TV من القائمة الرئيسية: يفتح حصة اليوم مباشرة، أو يعرض اختيارًا إن تعددت."""
    conn = get_conn()
    sel_date = request.args.get("date", date.today().isoformat())
    sql = """SELECT ts.*, g.name AS group_name, c.name AS coach_name,
             (SELECT COUNT(*) FROM attendance a WHERE a.training_session_id=ts.id AND a.status IN ('PRESENT','LATE')) AS present_count,
             (SELECT COUNT(*) FROM players p WHERE p.group_id=ts.group_id) AS roster_size
             FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id
             LEFT JOIN coaches c ON c.id=ts.coach_id WHERE ts.session_date=? AND ts.status!='CANCELLED'"""
    sql, params = _scope_sql(sql, [sel_date])
    sessions = q(conn, sql + " ORDER BY ts.start_time", tuple(params))
    conn.close()
    if len(sessions) == 1 and "pick" not in request.args:
        return redirect(f"/attendance/session/{sessions[0]['id']}/tv")
    return render_template("attendance_tv_pick.html", sessions=sessions, sel_date=sel_date)


def _finalize_session(conn, session_id, force=False):
    """Returns True if the session was closed, False if it needs the roster
    completed first (and force wasn't set)."""
    ts = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (session_id,))
    roster_count = q1(conn, "SELECT COUNT(*) c FROM players WHERE group_id=?", (ts["group_id"],))["c"]
    marked_count = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE training_session_id=?", (session_id,))["c"]
    if marked_count < roster_count and not force:
        flash(f"تنبيه: لم يتم تحضير {roster_count - marked_count} لاعب من القائمة. أكمل التحضير أولًا أو أكّد التجاوز.")
        return False
    ex(conn, "UPDATE training_sessions SET status='COMPLETED' WHERE id=?", (session_id,))
    audit_log(conn, g.user["id"], "COMPLETE_SESSION", "training_sessions", session_id)
    return True


@bp.route("/attendance/session/<int:session_id>/reopen", methods=["POST"])
@permission_required("take_attendance")
def reopen_session(session_id):
    """يحل مشكلة 'التحضير إذا وقف ما أقدر أخليه يبدأ من جديد': يعيد الحصة
    المُغلقة (مكتملة) إلى حالة مجدولة بحيث تظهر مرة أخرى في شاشة التحضير
    وفي 'يحتاج تدخل اليوم'، ويقدر المدرب يكمل/يصحح التحضير عليها من جديد.
    لا يمسّ أي سجل تحضير أو نقاط موجود مسبقًا — تغيير حالة فقط."""
    conn = get_conn()
    ts = q1(conn, "SELECT * FROM training_sessions WHERE id=?", (session_id,))
    if not ts:
        conn.close()
        abort(404)
    ex(conn, "UPDATE training_sessions SET status='SCHEDULED', checkin_closed_at=NULL WHERE id=?", (session_id,))
    audit_log(conn, g.user["id"], "REOPEN_SESSION", "training_sessions", session_id,
              before={"status": ts["status"]}, after={"status": "SCHEDULED"})
    conn.commit()
    conn.close()
    flash("تم إعادة فتح الحصة — يمكنك إكمال أو تعديل التحضير الآن ✅")
    return redirect(f"/attendance/session/{session_id}")


@bp.route("/attendance/session/<int:session_id>/complete", methods=["POST"])
@permission_required("take_attendance")
def complete(session_id):
    conn = get_conn()
    closed = _finalize_session(conn, session_id, force=request.form.get("force") == "1")
    conn.commit()
    conn.close()
    if closed:
        flash("تم إغلاق الحصة بنجاح ✅")
        return redirect("/attendance")
    return redirect(f"/attendance/session/{session_id}")


@bp.route("/attendance/session/<int:session_id>/end", methods=["GET", "POST"])
@permission_required("take_attendance")
def end_session(session_id):
    """لحظة فوق: شاشة سريعة واحدة (أقل من دقيقة) يختار فيها المدرب لاعب
    الحصة، يمنح/يخصم نقاطًا سريعة، يمنح إنجازًا، ويكتب ملاحظة مختصرة —
    ثم تُغلق الحصة فورًا."""
    conn = get_conn()
    ts = q1(conn, "SELECT ts.*, g.name as group_name FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id WHERE ts.id=?",
            (session_id,))
    if not ts:
        conn.close()
        abort(404)
    roster = q(conn, "SELECT id, first_name, last_name, photo_url FROM players WHERE group_id=? ORDER BY first_name",
               (ts["group_id"],))

    if request.method == "POST":
        already_completed = ts["status"] == "COMPLETED"
        pos_id = request.form.get("player_of_session") or None
        note = (request.form.get("note") or "").strip() or None
        amount_raw = request.form.get("amount") or "0"
        reason = (request.form.get("reason") or "").strip()
        target_player = request.form.get("points_player_id") or None
        manual_achievement = request.form.get("achievement") or None

        if not already_completed:
            if pos_id:
                ex(conn, "UPDATE training_sessions SET player_of_session_id=? WHERE id=?", (pos_id, session_id))
                ach.award_manual(conn, int(pos_id), "PLAYER_OF_SESSION", g.user["id"])
                try:
                    award_points(conn, int(pos_id), 10, "لاعب الحصة", "ACHIEVEMENT", g.user["id"])
                except PointsError:
                    pass
                ach.check_after_points(conn, int(pos_id), g.user["id"])

            try:
                amount = int(amount_raw)
            except ValueError:
                amount = 0
            if amount and target_player and reason:
                try:
                    award_points(conn, int(target_player), amount, reason, "ADMIN", g.user["id"])
                    ach.check_after_points(conn, int(target_player), g.user["id"])
                    if reason == "الروح الرياضية":
                        ach.award_manual(conn, int(target_player), "TEAM_SPIRIT", g.user["id"])
                except PointsError as e:
                    flash(str(e))

            if manual_achievement and target_player:
                ach.award_manual(conn, int(target_player), manual_achievement, g.user["id"])

            if note:
                ex(conn, "UPDATE training_sessions SET session_note=? WHERE id=?", (note, session_id))

        closed = _finalize_session(conn, session_id, force=True)
        conn.commit()
        conn.close()
        flash("تم إغلاق الحصة وتسجيل لحظة فوق بنجاح ✅")
        return redirect("/attendance")

    conn.close()
    return render_template("session_end.html", ts=ts, roster=roster, add_reasons=ADD_REASONS,
                            subtract_reasons=SUBTRACT_REASONS)


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
