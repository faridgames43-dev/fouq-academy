from flask import Blueprint, render_template, g, request
from datetime import date
from db import get_conn, q, q1
from business.rbac import roles_required, branch_scope
from business.dashboard import kpis, needs_attention_today
from business.notifications import run_daily_checks

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@roles_required("SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR")
def index():
    conn = get_conn()
    branch_id = branch_scope(g.user)
    run_daily_checks(conn)
    data = kpis(conn, branch_id)
    attention = needs_attention_today(conn, branch_id)
    branches = q(conn, "SELECT * FROM branches ORDER BY id")
    today_sessions = q(conn, """SELECT ts.*, g.name as group_name, c.name as coach_name,
                                (SELECT COUNT(*) FROM attendance a WHERE a.training_session_id=ts.id) as marked
                                FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id
                                LEFT JOIN coaches c ON c.id = ts.coach_id
                                WHERE ts.session_date = ?""", (date.today().isoformat(),))
    conn.commit()
    conn.close()
    return render_template("dashboard_admin.html", kpis=data, attention=attention, branches=branches,
                            today_sessions=today_sessions)


@bp.route("/coach")
@roles_required("COACH")
def coach_home():
    conn = get_conn()
    coach = q1(conn, "SELECT * FROM coaches WHERE user_id=?", (g.user["id"],))
    today_sessions = q(conn, """SELECT ts.*, g.name as group_name FROM training_sessions ts
                                JOIN groups_ g ON g.id=ts.group_id
                                WHERE ts.coach_id=? AND ts.session_date >= date('now') ORDER BY ts.session_date, ts.start_time LIMIT 5""",
                        (coach["id"] if coach else -1,))
    groups = q(conn, "SELECT * FROM groups_ WHERE coach_id=?", (coach["id"] if coach else -1,))
    players_count = q1(conn, "SELECT COUNT(*) c FROM players WHERE coach_id=?", (coach["id"] if coach else -1,))["c"]
    missing_assessments = q1(conn, """SELECT COUNT(*) c FROM players p WHERE p.coach_id=?
        AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.player_id=p.id AND a.assessment_date >= date('now','-60 day'))""",
                              (coach["id"] if coach else -1,))["c"]
    need_followup = q(conn, """SELECT p.id, p.first_name, p.last_name FROM players p WHERE p.coach_id=? AND p.id IN (
        SELECT player_id FROM attendance WHERE status='ABSENT' AND checked_at >= datetime('now','-14 day')
        GROUP BY player_id HAVING COUNT(*) >= 3)""", (coach["id"] if coach else -1,))
    unfinished = q(conn, """SELECT ts.*, g.name as group_name FROM training_sessions ts JOIN groups_ g ON g.id=ts.group_id
                            WHERE ts.coach_id=? AND ts.status IN ('SCHEDULED','STARTED') AND ts.session_date <= date('now')""",
                   (coach["id"] if coach else -1,))
    conn.close()
    return render_template("dashboard_coach.html", coach=coach, today_sessions=today_sessions, groups=groups,
                            players_count=players_count, missing_assessments=missing_assessments,
                            need_followup=need_followup, unfinished=unfinished)
