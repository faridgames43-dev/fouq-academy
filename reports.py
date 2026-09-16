"""One-click report data providers. Every report returns plain rows so the
same data can drive an HTML page, a CSV export, or a PDF export."""
import csv
import io
from db import q


def outstanding_sessions_report(conn, branch_id=None):
    bc = " AND p.branch_id = :b" if branch_id else ""
    rows = q(
        conn,
        f"""SELECT p.id, p.first_name, p.last_name, p.player_code,
               (SELECT status FROM subscriptions s WHERE s.player_id=p.id ORDER BY s.id DESC LIMIT 1) as sub_status,
               COALESCE(SUM(CASE WHEN se.type='REGULAR' THEN se.quantity_remaining ELSE 0 END),0) as regular_remaining,
               COALESCE(SUM(CASE WHEN se.type='COMPENSATION' THEN se.quantity_remaining ELSE 0 END),0) as comp_remaining,
               COALESCE(SUM(CASE WHEN se.type='BONUS' THEN se.quantity_remaining ELSE 0 END),0) as bonus_remaining,
               MIN(CASE WHEN se.type IN ('COMPENSATION','BONUS') AND se.status='ACTIVE' AND se.quantity_remaining>0 THEN se.expires_at END) as nearest_expiry,
               (SELECT MAX(ts.session_date) FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
                  WHERE a.player_id=p.id AND a.status IN ('PRESENT','LATE')) as last_attendance
        FROM players p
        LEFT JOIN session_entitlements se ON se.player_id=p.id AND se.status='ACTIVE'
        WHERE 1=1 {bc if branch_id else ''}
        GROUP BY p.id
        HAVING (regular_remaining + comp_remaining + bonus_remaining) > 0
        ORDER BY nearest_expiry IS NULL, nearest_expiry ASC""",
        {"b": branch_id} if branch_id else (),
    )
    return rows


def attendance_report(conn, date_from, date_to, branch_id=None, group_id=None):
    sql = """SELECT ts.session_date, ts.start_time, g.name as group_name, p.first_name, p.last_name,
                    p.player_code, a.status
             FROM attendance a
             JOIN training_sessions ts ON ts.id = a.training_session_id
             JOIN players p ON p.id = a.player_id
             JOIN groups_ g ON g.id = ts.group_id
             WHERE ts.session_date BETWEEN ? AND ?"""
    params = [date_from, date_to]
    if branch_id:
        sql += " AND ts.branch_id=?"
        params.append(branch_id)
    if group_id:
        sql += " AND ts.group_id=?"
        params.append(group_id)
    sql += " ORDER BY ts.session_date DESC, ts.start_time DESC"
    return q(conn, sql, tuple(params))


def subscriptions_report(conn, branch_id=None, status=None):
    sql = """SELECT s.*, p.first_name, p.last_name, p.player_code FROM subscriptions s
             JOIN players p ON p.id = s.player_id WHERE 1=1"""
    params = []
    if branch_id:
        sql += " AND p.branch_id=?"
        params.append(branch_id)
    if status:
        sql += " AND s.status=?"
        params.append(status)
    sql += " ORDER BY s.id DESC"
    return q(conn, sql, tuple(params))


def revenue_report(conn, branch_id=None):
    sql = """SELECT strftime('%Y-%m', s.created_at) as month, SUM(s.paid_amount) as revenue, COUNT(*) as subs
              FROM subscriptions s JOIN players p ON p.id=s.player_id WHERE 1=1"""
    params = []
    if branch_id:
        sql += " AND p.branch_id=?"
        params.append(branch_id)
    sql += " GROUP BY month ORDER BY month DESC"
    return q(conn, sql, tuple(params))


def coach_performance_report(conn, branch_id=None):
    sql = """SELECT c.id, c.name,
               (SELECT COUNT(*) FROM groups_ g WHERE g.coach_id=c.id) as groups_count,
               (SELECT COUNT(*) FROM players p WHERE p.coach_id=c.id) as players_count,
               (SELECT ROUND(AVG(CASE WHEN a.status IN ('PRESENT','LATE') THEN 100.0 ELSE 0 END),1)
                  FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
                  WHERE ts.coach_id=c.id) as avg_attendance,
               (SELECT COUNT(*) FROM assessments a2 WHERE a2.coach_id=c.id) as assessments_done
             FROM coaches c WHERE 1=1"""
    params = []
    if branch_id:
        sql += " AND c.branch_id=?"
        params.append(branch_id)
    return q(conn, sql, tuple(params))


def to_csv(rows):
    output = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return "﻿" + output.getvalue()  # BOM so Excel opens Arabic UTF-8 correctly
