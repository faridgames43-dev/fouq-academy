"""KPI + 'Needs Attention Today' aggregation for the main dashboards."""
from datetime import date, timedelta
from db import q, q1
from business.subscriptions import sync_subscription_statuses
from business.entitlements import sync_expirations


def _branch_clause(branch_id, alias="p"):
    if branch_id:
        return f" AND {alias}.branch_id = {int(branch_id)}"
    return ""


def kpis(conn, branch_id=None):
    sync_subscription_statuses(conn)
    sync_expirations(conn)
    bc = _branch_clause(branch_id)

    active_players = q1(conn, f"SELECT COUNT(*) c FROM players p WHERE p.status='ACTIVE'{bc}")["c"]
    new_players_30d = q1(
        conn, f"SELECT COUNT(*) c FROM players p WHERE p.join_date >= date('now','-30 day'){bc}"
    )["c"]

    today = date.today().isoformat()
    todays_sessions = q(conn, f"SELECT id FROM training_sessions ts WHERE ts.session_date=?{_branch_clause(branch_id,'ts')}", (today,))
    session_ids = [s["id"] for s in todays_sessions]
    attendance_today = 0
    absence_today = 0
    if session_ids:
        placeholders = ",".join(["?"] * len(session_ids))
        attendance_today = q1(conn, f"SELECT COUNT(*) c FROM attendance WHERE status IN ('PRESENT','LATE') AND training_session_id IN ({placeholders})", session_ids)["c"]
        absence_today = q1(conn, f"SELECT COUNT(*) c FROM attendance WHERE status='ABSENT' AND training_session_id IN ({placeholders})", session_ids)["c"]

    expiring_soon = q1(conn, f"""SELECT COUNT(*) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                                 WHERE s.status='EXPIRING_SOON'{bc}
                                 AND s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=s.player_id)""")["c"]
    expired = q1(conn, f"""SELECT COUNT(*) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                           WHERE s.status='EXPIRED'{bc}
                           AND s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=s.player_id)""")["c"]

    outstanding_comp = q1(conn, f"""SELECT COALESCE(SUM(se.quantity_remaining),0) c FROM session_entitlements se
                                    JOIN players p ON p.id=se.player_id
                                    WHERE se.status='ACTIVE' AND se.type IN ('COMPENSATION','BONUS','LEGACY'){bc}""")["c"]

    renewals_30d = q1(conn, f"""SELECT COUNT(*) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                                WHERE s.created_at >= datetime('now','-30 day'){bc}""")["c"]

    revenue_month = q1(conn, f"""SELECT COALESCE(SUM(s.paid_amount),0) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                                 WHERE strftime('%Y-%m', s.created_at) = strftime('%Y-%m','now'){bc}""")["c"]

    total_players_for_rate = q1(conn, f"SELECT COUNT(*) c FROM players p WHERE 1=1{bc}")["c"] or 1
    expired_30d = q1(conn, f"""SELECT COUNT(*) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                                WHERE s.status='EXPIRED' AND s.end_date >= date('now','-30 day'){bc}""")["c"]
    renewed_after_expiry = q1(conn, f"""SELECT COUNT(DISTINCT s1.player_id) c FROM subscriptions s1
                                        JOIN players p ON p.id = s1.player_id
                                        JOIN subscriptions s2 ON s2.player_id = s1.player_id AND s2.id > s1.id
                                        WHERE s1.status='EXPIRED'{bc}""")["c"]
    renewal_rate = round((renewed_after_expiry / expired_30d * 100), 1) if expired_30d else 0.0
    churn = round(100 - renewal_rate, 1) if expired_30d else 0.0

    avg_attendance = q1(conn, f"""SELECT ROUND(AVG(present),1) c FROM (
        SELECT a.player_id, 100.0*SUM(CASE WHEN a.status IN ('PRESENT','LATE') THEN 1 ELSE 0 END)/COUNT(*) as present
        FROM attendance a JOIN players p ON p.id=a.player_id
        WHERE 1=1{bc} GROUP BY a.player_id)""")["c"] or 0

    avg_dev = q1(conn, """SELECT ROUND(AVG(s.score),1) c FROM assessment_scores s""")["c"] or 0

    rewards_pending = q1(conn, "SELECT COUNT(*) c FROM reward_redemptions WHERE status='PENDING'")["c"]

    return {
        "active_players": active_players,
        "new_players": new_players_30d,
        "attendance_today": attendance_today,
        "absence_today": absence_today,
        "expiring_soon": expiring_soon,
        "expired": expired,
        "outstanding_compensation": outstanding_comp,
        "renewals_30d": renewals_30d,
        "revenue_month": revenue_month,
        "renewal_rate": renewal_rate,
        "churn": churn,
        "avg_attendance": avg_attendance,
        "avg_development": avg_dev,
        "rewards_pending": rewards_pending,
    }


def needs_attention_today(conn, branch_id=None):
    bc = _branch_clause(branch_id)
    items = []

    expiring_7 = q1(conn, f"""SELECT COUNT(*) c FROM subscriptions s JOIN players p ON p.id=s.player_id
                              WHERE s.status='EXPIRING_SOON'{bc}
                              AND s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=s.player_id)""")["c"]
    if expiring_7:
        items.append({"icon": "⏳", "text": f"{expiring_7} اشتراك سينتهي خلال 7 أيام", "href": "/renewals"})

    expired_with_comp = q1(conn, f"""SELECT COUNT(DISTINCT p.id) c FROM players p
        JOIN subscriptions s ON s.player_id=p.id AND s.id=(SELECT MAX(id) FROM subscriptions s2 WHERE s2.player_id=p.id)
        JOIN session_entitlements se ON se.player_id=p.id AND se.status='ACTIVE' AND se.type IN ('COMPENSATION','BONUS','LEGACY') AND se.quantity_remaining>0
        WHERE s.status='EXPIRED'{bc}""")["c"]
    if expired_with_comp:
        items.append({"icon": "🎯", "text": f"{expired_with_comp} لاعب انتهى اشتراكه ولديه حصص مستحقة", "href": "/renewals"})

    no_sessions = q1(conn, f"""SELECT COUNT(DISTINCT p.id) c FROM players p
        WHERE p.status='ACTIVE'{bc} AND NOT EXISTS (
            SELECT 1 FROM session_entitlements se WHERE se.player_id=p.id AND se.status='ACTIVE' AND se.quantity_remaining>0)""")["c"]
    if no_sessions:
        items.append({"icon": "🛑", "text": f"{no_sessions} لاعب نفدت جميع حصصه", "href": "/renewals"})

    missing_assessment = q1(conn, f"""SELECT COUNT(*) c FROM players p WHERE p.status='ACTIVE'{bc}
        AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.player_id=p.id AND a.assessment_date >= date('now','-60 day'))""")["c"]
    if missing_assessment:
        items.append({"icon": "📝", "text": f"{missing_assessment} لاعبين يحتاجون تقييمًا", "href": "/players"})

    unfinished_sessions = q1(conn, f"""SELECT COUNT(*) c FROM training_sessions ts WHERE ts.session_date <= date('now')
        AND ts.status IN ('SCHEDULED','STARTED'){_branch_clause(branch_id,'ts')}""")["c"]
    if unfinished_sessions:
        items.append({"icon": "⏱️", "text": f"{unfinished_sessions} حصة لم تُغلق من المدرب", "href": "/attendance"})

    pending_redemptions = q1(conn, "SELECT COUNT(*) c FROM reward_redemptions WHERE status='PENDING'")["c"]
    if pending_redemptions:
        items.append({"icon": "🎁", "text": f"{pending_redemptions} طلب استبدال جائزة بانتظار الموافقة", "href": "/rewards"})

    frequent_absence = q1(conn, f"""SELECT COUNT(*) c FROM (
        SELECT a.player_id FROM attendance a JOIN players p ON p.id=a.player_id
        WHERE a.status='ABSENT' AND a.checked_at >= datetime('now','-14 day'){bc}
        GROUP BY a.player_id HAVING COUNT(*) >= 3)""")["c"]
    if frequent_absence:
        items.append({"icon": "⚠️", "text": f"{frequent_absence} لاعب غاب 3 مرات أو أكثر", "href": "/retention"})

    return items
