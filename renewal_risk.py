"""Rules-Based Renewal Risk - explicitly NOT an AI/ML score. A transparent
point-count of well-defined operational risk factors."""
from datetime import date
from db import q, q1
from business.levels import attendance_rate
from business.subscriptions import get_latest_subscription
from business.entitlements import get_balances


def compute_risk(conn, player_id):
    reasons = []
    score = 0

    att = attendance_rate(conn, player_id, window_days=30)
    if att < 60:
        score += 1
        reasons.append(f"انخفاض الحضور خلال آخر 30 يومًا ({att}٪)")

    sub = get_latest_subscription(conn, player_id)
    if sub:
        try:
            end = date.fromisoformat(sub["end_date"])
            days_left = (end - date.today()).days
            if -3650 < days_left <= 7:
                score += 1
                reasons.append("اشتراك قارب على الانتهاء أو منتهٍ")
        except Exception:
            pass

    balances = get_balances(conn, player_id)
    if balances["TOTAL"] <= 2:
        score += 1
        reasons.append("قلة الحصص المتبقية")

    last_assessment = q1(conn, "SELECT MAX(assessment_date) as d FROM assessments WHERE player_id=?", (player_id,))
    if not last_assessment or not last_assessment["d"]:
        score += 1
        reasons.append("لا يوجد تقييم حديث")
    else:
        try:
            days_since = (date.today() - date.fromisoformat(last_assessment["d"])).days
            if days_since > 60:
                score += 1
                reasons.append("لم يتم تقييمه منذ أكثر من 60 يومًا")
        except Exception:
            pass

    last_contact = q1(
        conn, "SELECT MAX(created_at) as d FROM renewal_notes WHERE player_id=?", (player_id,)
    )
    if last_contact and last_contact["d"]:
        note = q1(conn, "SELECT * FROM renewal_notes WHERE player_id=? ORDER BY id DESC LIMIT 1", (player_id,))
        if note and note["stage"] == "CONTACTED" and note["outcome"] is None:
            score += 1
            reasons.append("تم التواصل ولم يجدد بعد")

    level = "LOW"
    if score >= 3:
        level = "HIGH"
    elif score >= 2:
        level = "MEDIUM"
    return {"score": score, "level": level, "reasons": reasons}
