usiness/assessments.py"""Player Development System: Skill / Fitness / Behavior / Discipline.
Assessment vs Gamification vs Rewards Currency are kept strictly separate -
FOUQ Points never influence the technical rating."""
from db import q, q1, ex
from business.settings_lib import get_setting_json
from business.audit import log as audit_log

CATEGORY_LABELS = {"SKILL": "المهاري", "FITNESS": "اللياقي", "BEHAVIOR": "السلوكي", "DISCIPLINE": "الانضباط"}

CHILD_FRIENDLY_BANDS = [
    (0, 40, "في الطريق"),
    (40, 65, "يتقدم"),
    (65, 85, "متقن"),
    (85, 101, "متميز"),
]


def child_label(score):
    for lo, hi, label in CHILD_FRIENDLY_BANDS:
        if lo <= score < hi:
            return label
    return "في الطريق"


def create_assessment(conn, player_id, atype, coach_id, scores: dict, notes=None, user_id=None, assessment_date=None):
    """scores: {metric_id: score}"""
    aid = ex(
        conn,
        "INSERT INTO assessments(player_id, type, coach_id, notes, assessment_date) VALUES (?,?,?,?,COALESCE(?,date('now')))",
        (player_id, atype, coach_id, notes, assessment_date),
    )
    for metric_id, score in scores.items():
        ex(conn, "INSERT INTO assessment_scores(assessment_id, metric_id, score) VALUES (?,?,?)",
           (aid, metric_id, score))
    audit_log(conn, user_id, "CREATE_ASSESSMENT", "assessments", aid,
              after={"player_id": player_id, "type": atype, "scores": scores}, reason=notes)
    return aid


def get_category_average(conn, assessment_id, category_code):
    row = q1(
        conn,
        """SELECT AVG(s.score) as avg_score FROM assessment_scores s
           JOIN assessment_metrics m ON m.id = s.metric_id
           JOIN assessment_categories c ON c.id = m.category_id
           WHERE s.assessment_id=? AND c.code=?""",
        (assessment_id, category_code),
    )
    return round(row["avg_score"], 1) if row and row["avg_score"] is not None else None


def get_overall(conn, assessment_id):
    weights = get_setting_json(conn, "assessment_weights", {"SKILL": 40, "FITNESS": 25, "BEHAVIOR": 20, "DISCIPLINE": 15})
    total_weight = 0
    weighted_sum = 0
    breakdown = {}
    for code, w in weights.items():
        avg = get_category_average(conn, assessment_id, code)
        breakdown[code] = avg
        if avg is not None:
            weighted_sum += avg * w
            total_weight += w
    overall = round(weighted_sum / total_weight, 1) if total_weight else None
    return overall, breakdown


def latest_assessment(conn, player_id, atype=None):
    sql = "SELECT * FROM assessments WHERE player_id=?"
    params = [player_id]
    if atype:
        sql += " AND type=?"
        params.append(atype)
    sql += " ORDER BY assessment_date DESC, id DESC LIMIT 1"
    return q1(conn, sql, tuple(params))


def player_development_timeline(conn, player_id):
    assessments = q(conn, "SELECT * FROM assessments WHERE player_id=? ORDER BY assessment_date ASC, id ASC",
                     (player_id,))
    timeline = []
    for a in assessments:
        overall, breakdown = get_overall(conn, a["id"])
        timeline.append({"assessment": a, "overall": overall, "breakdown": breakdown})
    return timeline
