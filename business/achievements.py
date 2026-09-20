"""Rule-based (not AI, not random) achievement checks - run after the
events that could trigger them: attendance, points, renewal, levels."""
from db import q, q1, ex
from business.points import award_points
from business.audit import log as audit_log


def _award_if_new(conn, player_id, code, user_id=None):
    ach = q1(conn, "SELECT * FROM achievements WHERE code=?", (code,))
    if not ach:
        return None
    existing = q1(conn, "SELECT id FROM player_achievements WHERE player_id=? AND achievement_id=?",
                  (player_id, ach["id"]))
    if existing:
        return None
    pa_id = ex(conn, "INSERT INTO player_achievements(player_id, achievement_id) VALUES (?,?)",
               (player_id, ach["id"]))
    if ach["points_reward"]:
        award_points(conn, player_id, ach["points_reward"], f"إنجاز: {ach['name']}", "ACHIEVEMENT", user_id)
    audit_log(conn, user_id, "AWARD_ACHIEVEMENT", "player_achievements", pa_id,
              after={"player_id": player_id, "achievement": code})
    return pa_id


def check_after_attendance(conn, player_id, user_id=None):
    total_present = q1(
        conn, "SELECT COUNT(*) as c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')",
        (player_id,),
    )["c"]
    newly = []
    if total_present >= 1:
        if _award_if_new(conn, player_id, "FIRST_SESSION", user_id):
            newly.append("FIRST_SESSION")
    if total_present >= 10:
        if _award_if_new(conn, player_id, "COMMITTED_10", user_id):
            newly.append("COMMITTED_10")

    # 5 consecutive attended sessions with no ABSENT in between (last 5 chronological)
    last5 = q(
        conn,
        """SELECT a.status FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
           WHERE a.player_id=? ORDER BY ts.session_date DESC, a.id DESC LIMIT 5""",
        (player_id,),
    )
    if len(last5) == 5 and all(r["status"] in ("PRESENT", "LATE") for r in last5):
        if _award_if_new(conn, player_id, "STREAK_5", user_id):
            newly.append("STREAK_5")
    newly += check_perfect_attendance(conn, player_id, user_id)
    newly += check_tenure(conn, player_id, user_id)
    return newly


def check_after_points(conn, player_id, user_id=None):
    from business.points import get_balance
    bal = get_balance(conn, player_id)
    newly = []
    if bal >= 100:
        if _award_if_new(conn, player_id, "FIRST_100", user_id):
            newly.append("FIRST_100")
    return newly


def check_after_renewal(conn, player_id, user_id=None):
    count = q1(conn, "SELECT COUNT(*) as c FROM subscriptions WHERE player_id=?", (player_id,))["c"]
    newly = []
    if count >= 3:
        if _award_if_new(conn, player_id, "LOYAL_3X", user_id):
            newly.append("LOYAL_3X")
    return newly


def check_after_promotion(conn, player_id, user_id=None):
    count = q1(conn, "SELECT COUNT(*) as c FROM player_levels WHERE player_id=?", (player_id,))["c"]
    newly = []
    if count >= 2:
        if _award_if_new(conn, player_id, "FIRST_PROMOTION", user_id):
            newly.append("FIRST_PROMOTION")
    return newly


def check_development_leap(conn, player_id, prev_overall, new_overall, user_id=None):
    newly = []
    if prev_overall is not None and new_overall is not None and (new_overall - prev_overall) >= 15:
        if _award_if_new(conn, player_id, "DEVELOPMENT_LEAP", user_id):
            newly.append("DEVELOPMENT_LEAP")
    return newly


def award_manual(conn, player_id, code, user_id):
    return _award_if_new(conn, player_id, code, user_id)


def recheck_after_attendance_cancel(conn, player_id, user_id=None):
    """After an attendance record is cancelled (single, bulk-selected, or
    bulk-all), a purely attendance-COUNT-based achievement the player had
    already earned may no longer be valid — e.g. FIRST_SESSION was granted
    after their only PRESENT record, and that record just got cancelled.
    Revoke it and reverse the points it awarded via the existing REVERSAL
    ledger mechanism (never a silent balance edit / never a deleted log
    row), so رصيد فوق always matches the honest attendance history.

    Only the two strictly monotonic, deterministic thresholds are
    reconsidered here (FIRST_SESSION, COMMITTED_10); order/time-dependent
    ones (STREAK_5, PERFECT_ATTENDANCE) are intentionally left as-earned,
    matching how most gamification systems treat streak badges."""
    total_present = q1(
        conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')",
        (player_id,),
    )["c"]
    revoked = []
    for code, still_valid in (("FIRST_SESSION", total_present >= 1), ("COMMITTED_10", total_present >= 10)):
        if still_valid:
            continue
        ach_row = q1(conn, "SELECT * FROM achievements WHERE code=?", (code,))
        if not ach_row:
            continue
        pa = q1(conn, "SELECT * FROM player_achievements WHERE player_id=? AND achievement_id=?",
                (player_id, ach_row["id"]))
        if not pa:
            continue
        if ach_row["points_reward"]:
            txn = q1(
                conn,
                """SELECT id FROM points_transactions WHERE player_id=? AND category='ACHIEVEMENT'
                   AND reason=? ORDER BY id DESC LIMIT 1""",
                (player_id, f"إنجاز: {ach_row['name']}"),
            )
            if txn:
                from business.points import cancel_points_transaction, PointsError
                try:
                    cancel_points_transaction(conn, txn["id"], user_id)
                except PointsError:
                    pass
        ex(conn, "DELETE FROM player_achievements WHERE id=?", (pa["id"],))
        audit_log(conn, user_id, "REVOKE_ACHIEVEMENT", "player_achievements", pa["id"],
                  before={"player_id": player_id, "achievement": code}, reason="إلغاء تحضير أثّر على الأهلية")
        revoked.append(code)
    return revoked


def check_perfect_attendance(conn, player_id, user_id=None):
    """حضور كامل: لا غياب واحد خلال الشهر الحالي (بحد أدنى 4 حصص مسجّلة)."""
    from datetime import date
    month_start = date.today().replace(day=1).isoformat()
    rows = q(
        conn,
        """SELECT a.status FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
           WHERE a.player_id=? AND ts.session_date >= ?""",
        (player_id, month_start),
    )
    newly = []
    if len(rows) >= 4 and all(r["status"] in ("PRESENT", "LATE") for r in rows):
        if _award_if_new(conn, player_id, "PERFECT_ATTENDANCE", user_id):
            newly.append("PERFECT_ATTENDANCE")
    return newly


def check_tenure(conn, player_id, user_id=None):
    """30 يومًا مع فوق: مرور 30 يومًا على تاريخ الانضمام."""
    from datetime import date
    player = q1(conn, "SELECT join_date FROM players WHERE id=?", (player_id,))
    newly = []
    if player and player["join_date"]:
        try:
            joined = date.fromisoformat(player["join_date"])
            if (date.today() - joined).days >= 30:
                if _award_if_new(conn, player_id, "TENURE_30", user_id):
                    newly.append("TENURE_30")
        except Exception:
            pass
    return newly


def check_skill_growth(conn, player_id, prev_skill_avg, new_skill_avg, user_id=None):
    """تطور مهاري: تحسّن واضح في محور المهاري تحديدًا (لا التقييم العام)."""
    newly = []
    if prev_skill_avg is not None and new_skill_avg is not None and (new_skill_avg - prev_skill_avg) >= 10:
        if _award_if_new(conn, player_id, "SKILL_GROWTH", user_id):
            newly.append("SKILL_GROWTH")
    return newly


EXTRA_ACHIEVEMENTS = [
    ("PERFECT_ATTENDANCE", "حضور كامل", "حضور 100% خلال الشهر الحالي بدون أي غياب", "🌟", 20),
    ("TENURE_30", "30 يومًا مع فوق", "مرور 30 يومًا على الانضمام لأكاديمية فوق", "📅", 15),
    ("SKILL_GROWTH", "تطور مهاري", "تحسّن ملحوظ في التقييم المهاري", "⚽", 20),
    ("PLAYER_OF_SESSION", "لاعب الحصة", "تم اختياره لاعب الحصة", "🏅", 10),
]


def ensure_extra_achievements(conn):
    """Idempotent: insert any achievement code that doesn't exist yet, and
    align a couple of names with the exact wording the academy wants —
    never touches an achievement a player has already earned differently."""
    for code, name, desc, icon, pr in EXTRA_ACHIEVEMENTS:
        existing = q1(conn, "SELECT id FROM achievements WHERE code=?", (code,))
        if not existing:
            ex(conn, "INSERT INTO achievements(code,name,description,icon,points_reward) VALUES (?,?,?,?,?)",
               (code, name, desc, icon, pr))
    # "روح الفريق" -> "روح رياضية" per spec wording (same code/history, just the label)
    ex(conn, "UPDATE achievements SET name='روح رياضية', description='لحظة روح رياضية مميزة' WHERE code='TEAM_SPIRIT'")
