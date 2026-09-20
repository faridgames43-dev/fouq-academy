"""تسليم مهام اللاعبين (Player Task Submissions): المدرب/الإدارة ينشئ مهمة
لمجموعة معيّنة (عنوان + وصف + موعد اختياري + نقاط مكافأة)، واللاعب يرفع
ملف/فيديو من حسابه كإثبات تنفيذ. كل تسليم يُختم بتوقيت دقيق (submitted_at)
يظهر للمدرب. عند اعتماد التسليم تُمنح نقاط رصيد فوق تلقائيًا — نفس فكرة
نظام التحديات (challenges) القائم، وبنفس آلية عدم الحذف: رفض/إعادة تسليم
يُحدّث حالة السجل ولا يمسح تاريخ المحاولات السابقة من سجل النقاط."""
from db import q, q1, ex
from business.points import award_points
from business.audit import log as audit_log


class AssignmentError(Exception):
    pass


def create_assignment(conn, group_id, title, description, due_date, points_reward, user_id):
    aid = ex(
        conn,
        """INSERT INTO assignments(group_id, title, description, due_date, points_reward, created_by)
           VALUES (?,?,?,?,?,?)""",
        (group_id, title, description, due_date or None, points_reward or 0, user_id),
    )
    audit_log(conn, user_id, "CREATE_ASSIGNMENT", "assignments", aid, after={"group_id": group_id, "title": title})
    return aid


def set_assignment_active(conn, assignment_id, active, user_id):
    ex(conn, "UPDATE assignments SET active=? WHERE id=?", (1 if active else 0, assignment_id))
    audit_log(conn, user_id, "TOGGLE_ASSIGNMENT", "assignments", assignment_id, after={"active": active})


def list_assignments_for_group(conn, group_id, active_only=True):
    sql = "SELECT * FROM assignments WHERE group_id=?"
    params = [group_id]
    if active_only:
        sql += " AND active=1"
    sql += " ORDER BY id DESC"
    return q(conn, sql, tuple(params))


def list_assignments_scoped(conn, branch_id=None, coach_group_ids=None):
    sql = """SELECT a.*, g.name as group_name,
                    (SELECT COUNT(*) FROM players p WHERE p.group_id=a.group_id) as roster_size,
                    (SELECT COUNT(*) FROM assignment_submissions s WHERE s.assignment_id=a.id) as submitted_count,
                    (SELECT COUNT(*) FROM assignment_submissions s WHERE s.assignment_id=a.id AND s.status='PENDING') as pending_count
             FROM assignments a JOIN groups_ g ON g.id=a.group_id WHERE a.active=1"""
    params = []
    if coach_group_ids is not None:
        if not coach_group_ids:
            return []
        placeholders = ",".join(["?"] * len(coach_group_ids))
        sql += f" AND a.group_id IN ({placeholders})"
        params += coach_group_ids
    elif branch_id:
        sql += " AND g.branch_id=?"
        params.append(branch_id)
    sql += " ORDER BY a.id DESC"
    return q(conn, sql, tuple(params))


def get_assignment(conn, assignment_id):
    return q1(conn, "SELECT a.*, g.name as group_name FROM assignments a JOIN groups_ g ON g.id=a.group_id WHERE a.id=?",
              (assignment_id,))


def get_submission(conn, assignment_id, player_id):
    return q1(conn, "SELECT * FROM assignment_submissions WHERE assignment_id=? AND player_id=?",
              (assignment_id, player_id))


def roster_with_submissions(conn, assignment_id, group_id):
    players = q(conn, "SELECT id, first_name, last_name, photo_url FROM players WHERE group_id=? ORDER BY first_name",
                (group_id,))
    subs = {s["player_id"]: s for s in q(
        conn, """SELECT s.*, u.name as reviewer_name FROM assignment_submissions s
                 LEFT JOIN users u ON u.id=s.reviewed_by WHERE s.assignment_id=?""", (assignment_id,))}
    for p in players:
        p["submission"] = subs.get(p["id"])
    return players


def player_assignments_with_status(conn, player_id, group_id):
    assignments = list_assignments_for_group(conn, group_id)
    subs = {s["assignment_id"]: s for s in q(
        conn, "SELECT * FROM assignment_submissions WHERE player_id=?", (player_id,))}
    for a in assignments:
        a["submission"] = subs.get(a["id"])
    return assignments


def submit_assignment(conn, assignment_id, player_id, file_url, file_type, user_id=None):
    """Insert or re-submit (a rejected/pending submission can be replaced by
    a fresh upload; an already-APPROVED one cannot, so an already-rewarded
    submission is never silently overwritten)."""
    existing = get_submission(conn, assignment_id, player_id)
    if existing and existing["status"] == "APPROVED":
        raise AssignmentError("تم اعتماد هذه المهمة مسبقًا ولا يمكن إعادة تسليمها")
    if existing:
        ex(
            conn,
            """UPDATE assignment_submissions SET file_url=?, file_type=?, submitted_at=datetime('now'),
               status='PENDING', reviewed_by=NULL, reviewed_at=NULL, feedback_note=NULL WHERE id=?""",
            (file_url, file_type, existing["id"]),
        )
        sub_id = existing["id"]
    else:
        sub_id = ex(
            conn,
            """INSERT INTO assignment_submissions(assignment_id, player_id, file_url, file_type)
               VALUES (?,?,?,?)""",
            (assignment_id, player_id, file_url, file_type),
        )
    audit_log(conn, user_id, "SUBMIT_ASSIGNMENT", "assignment_submissions", sub_id,
              after={"player_id": player_id, "assignment_id": assignment_id})
    return sub_id


def review_submission(conn, submission_id, status, user_id, feedback_note=None):
    if status not in ("APPROVED", "REJECTED"):
        raise AssignmentError("حالة غير صالحة")
    sub = q1(conn, "SELECT * FROM assignment_submissions WHERE id=?", (submission_id,))
    if not sub:
        raise AssignmentError("التسليم غير موجود")
    ex(
        conn,
        """UPDATE assignment_submissions SET status=?, reviewed_by=?, reviewed_at=datetime('now'), feedback_note=?
           WHERE id=?""",
        (status, user_id, feedback_note, submission_id),
    )
    audit_log(conn, user_id, "REVIEW_ASSIGNMENT_SUBMISSION", "assignment_submissions", submission_id,
              after={"status": status}, reason=feedback_note)
    if status == "APPROVED" and sub["status"] != "APPROVED":
        assignment = q1(conn, "SELECT * FROM assignments WHERE id=?", (sub["assignment_id"],))
        if assignment and assignment["points_reward"]:
            award_points(conn, sub["player_id"], assignment["points_reward"],
                          f"مهمة مكتملة: {assignment['title']}", "ASSIGNMENT", user_id)
            from business.achievements import check_after_points
            check_after_points(conn, sub["player_id"], user_id)
    return submission_id
