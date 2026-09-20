"""لحظة فوق / التحدي الحالي: تحديات صغيرة قابلة للتحقيق تُبقي اللاعب متحمسًا،
تُمنح تلقائيًا وتُستبدل بتحدٍّ جديد بعد إنجازها. القواعد صريحة وليست عشوائية
في نتيجتها (فقط اختيار التحدي التالي عشوائي من قائمة نشطة)."""
from db import q, q1, ex
from business.points import award_points
from business.audit import log as audit_log

DEFAULT_CHALLENGES = [
    ("STREAK_3", "ثلاث حضورات متتالية", "احضر 3 حصص متتالية بدون غياب", "🔥", 15, "ATTENDANCE_STREAK", 3),
    ("STREAK_7", "أسبوع الانضباط", "احضر 7 حصص متتالية بدون غياب", "🛡️", 25, "ATTENDANCE_STREAK", 7),
    ("EARN_30", "اجمع 30 نقطة", "اجمع 30 نقطة فوق جديدة", "⭐", 10, "POINTS_EARN", 30),
    ("EARN_50", "اجمع 50 نقطة", "اجمع 50 نقطة فوق جديدة", "🌟", 15, "POINTS_EARN", 50),
]


def ensure_default_challenges(conn):
    for code, title, desc, icon, reward, ttype, tval in DEFAULT_CHALLENGES:
        existing = q1(conn, "SELECT id FROM challenges WHERE code=?", (code,))
        if not existing:
            ex(
                conn,
                """INSERT INTO challenges(code,title,description,icon,points_reward,target_type,target_value)
                   VALUES (?,?,?,?,?,?,?)""",
                (code, title, desc, icon, reward, ttype, tval),
            )


def get_current_challenge(conn, player_id):
    row = q1(
        conn,
        """SELECT pc.*, c.code, c.title, c.description, c.icon, c.points_reward, c.target_type, c.target_value
           FROM player_challenges pc JOIN challenges c ON c.id=pc.challenge_id
           WHERE pc.player_id=? AND pc.status='ACTIVE' ORDER BY pc.id DESC LIMIT 1""",
        (player_id,),
    )
    if row:
        return row
    candidate = q1(
        conn,
        """SELECT * FROM challenges WHERE active=1 AND id NOT IN (
               SELECT challenge_id FROM player_challenges WHERE player_id=? AND status='COMPLETED'
               AND completed_at >= datetime('now','-14 day'))
           ORDER BY RANDOM() LIMIT 1""",
        (player_id,),
    )
    if not candidate:
        candidate = q1(conn, "SELECT * FROM challenges WHERE active=1 ORDER BY RANDOM() LIMIT 1")
    if not candidate:
        return None
    ex(conn, "INSERT INTO player_challenges(player_id, challenge_id) VALUES (?,?)", (player_id, candidate["id"]))
    return get_current_challenge(conn, player_id)


def challenge_progress(conn, player_id, challenge_row):
    ttype = challenge_row["target_type"]
    target = challenge_row["target_value"]
    if ttype == "ATTENDANCE_STREAK":
        rows = q(
            conn,
            """SELECT a.status FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
               WHERE a.player_id=? AND a.checked_at >= ? ORDER BY ts.session_date DESC, a.id DESC LIMIT ?""",
            (player_id, challenge_row["assigned_at"], target),
        )
        streak = 0
        for r in rows:
            if r["status"] in ("PRESENT", "LATE"):
                streak += 1
            else:
                break
        return min(streak, target), target
    if ttype == "POINTS_EARN":
        row = q1(
            conn,
            """SELECT COALESCE(SUM(amount),0) c FROM points_transactions
               WHERE player_id=? AND amount>0 AND created_at >= ?""",
            (player_id, challenge_row["assigned_at"]),
        )
        earned = row["c"] if row else 0
        return min(earned, target), target
    return 0, target


def check_and_complete(conn, player_id, user_id=None):
    """Call after attendance/points events. Completes + rewards the current
    challenge and lets the next call assign a fresh one."""
    current = get_current_challenge(conn, player_id)
    if not current:
        return None
    progress, target = challenge_progress(conn, player_id, current)
    if progress >= target:
        ex(conn, "UPDATE player_challenges SET status='COMPLETED', completed_at=datetime('now') WHERE id=?",
           (current["id"],))
        if current["points_reward"]:
            award_points(conn, player_id, current["points_reward"], f"تحدٍ مكتمل: {current['title']}",
                          "CHALLENGE", user_id)
        audit_log(conn, user_id, "COMPLETE_CHALLENGE", "player_challenges", current["id"],
                  after={"player_id": player_id, "challenge": current["code"]})
        return current
    return None
