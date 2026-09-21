import os
import uuid
from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1, DATA_DIR
from business.rbac import login_required, permission_required, coach_group_ids, branch_scope, parent_player_ids
from business import assignments as asg

bp = Blueprint("assignments_bp", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Persistent disk (see players.py's UPLOAD_DIR comment) — not static/, which
# is wiped on every redeploy/restart/spin-down.
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads", "assignments")

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webm", ".m4v", ".pdf"}
MAX_SIZE = 25 * 1024 * 1024  # 25MB — كافٍ لفيديو قصير من الجوال


def _save_submission_file(file_storage):
    if not file_storage or not file_storage.filename:
        raise asg.AssignmentError("اختر ملفًا أو فيديو للتسليم")
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise asg.AssignmentError("نوع الملف غير مدعوم. المسموح: صور، فيديو (mp4/mov/webm)، أو PDF")
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_SIZE:
        raise asg.AssignmentError("حجم الملف أكبر من 25 ميجابايت")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fname = f"sub-{uuid.uuid4().hex[:12]}{ext}"
    out_path = os.path.join(UPLOAD_DIR, fname)
    file_storage.save(out_path)
    if ext in (".mp4", ".mov", ".webm", ".m4v"):
        file_type = "video"
    elif ext == ".pdf":
        file_type = "pdf"
    else:
        file_type = "image"
    return f"/uploads/assignments/{fname}", file_type


@bp.route("/uploads/assignments/<path:filename>")
@login_required
def assignment_upload(filename):
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)


# ---------------------------------------------------------------- staff ----

@bp.route("/assignments")
@permission_required("manage_assignments")
def admin_index():
    conn = get_conn()
    if g.user["role"] == "COACH":
        gids = coach_group_ids(conn, g.user["id"])
        items = asg.list_assignments_scoped(conn, coach_group_ids=gids)
    else:
        items = asg.list_assignments_scoped(conn, branch_id=branch_scope(g.user))
    groups = q(conn, "SELECT * FROM groups_ ORDER BY name")
    if g.user["role"] == "COACH":
        gids = coach_group_ids(conn, g.user["id"])
        groups = [gr for gr in groups if gr["id"] in gids]
    conn.close()
    return render_template("assignments_admin.html", assignments=items, groups=groups)


@bp.route("/assignments/new", methods=["GET", "POST"])
@permission_required("manage_assignments")
def new_assignment():
    conn = get_conn()
    groups = q(conn, "SELECT * FROM groups_ ORDER BY name")
    if g.user["role"] == "COACH":
        gids = coach_group_ids(conn, g.user["id"])
        groups = [gr for gr in groups if gr["id"] in gids]
    if request.method == "POST":
        f = request.form
        group_id = f.get("group_id")
        if g.user["role"] == "COACH" and int(group_id) not in coach_group_ids(conn, g.user["id"]):
            conn.close()
            abort(403)
        try:
            points_reward = int(f.get("points_reward") or 0)
        except ValueError:
            points_reward = 0
        asg.create_assignment(conn, group_id, f.get("title"), f.get("description"),
                               f.get("due_date") or None, points_reward, g.user["id"])
        conn.commit()
        conn.close()
        flash("تم إنشاء المهمة وإرسالها للمجموعة")
        return redirect("/assignments")
    conn.close()
    return render_template("assignment_form.html", groups=groups)


@bp.route("/assignments/<int:assignment_id>")
@permission_required("manage_assignments")
def assignment_detail(assignment_id):
    conn = get_conn()
    assignment = asg.get_assignment(conn, assignment_id)
    if not assignment:
        conn.close()
        abort(404)
    if g.user["role"] == "COACH" and assignment["group_id"] not in coach_group_ids(conn, g.user["id"]):
        conn.close()
        abort(403)
    roster = asg.roster_with_submissions(conn, assignment_id, assignment["group_id"])
    conn.close()
    return render_template("assignment_detail.html", assignment=assignment, roster=roster)


@bp.route("/assignments/submission/<int:submission_id>/review/<status>", methods=["POST"])
@permission_required("manage_assignments")
def review(submission_id, status):
    conn = get_conn()
    sub = q1(conn, "SELECT * FROM assignment_submissions WHERE id=?", (submission_id,))
    if not sub:
        conn.close()
        abort(404)
    assignment = asg.get_assignment(conn, sub["assignment_id"])
    if g.user["role"] == "COACH" and assignment["group_id"] not in coach_group_ids(conn, g.user["id"]):
        conn.close()
        abort(403)
    try:
        asg.review_submission(conn, submission_id, status.upper(), g.user["id"], request.form.get("note"))
        conn.commit()
        flash("تم اعتماد التسليم ومنح النقاط ✅" if status.upper() == "APPROVED" else "تم رفض التسليم")
    except asg.AssignmentError as e:
        flash(str(e))
    conn.close()
    return redirect(f"/assignments/{sub['assignment_id']}")


@bp.route("/assignments/<int:assignment_id>/toggle", methods=["POST"])
@permission_required("manage_assignments")
def toggle_assignment(assignment_id):
    conn = get_conn()
    assignment = asg.get_assignment(conn, assignment_id)
    if not assignment:
        conn.close()
        abort(404)
    asg.set_assignment_active(conn, assignment_id, not assignment["active"], g.user["id"])
    conn.commit()
    conn.close()
    flash("تم إيقاف المهمة" if assignment["active"] else "تم تفعيل المهمة مجددًا")
    return redirect("/assignments")


# ------------------------------------------------------------ player/parent ----

def _resolve_player_for_view(conn):
    """PLAYER: their own record. PARENT: the linked child from ?player_id=,
    verified via parent_player_ids (never trusted blindly)."""
    if g.user["role"] == "PLAYER":
        return q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    if g.user["role"] == "PARENT":
        player_id = request.args.get("player_id") or request.form.get("player_id")
        owned = parent_player_ids(conn, g.user["id"])
        try:
            pid = int(player_id) if player_id else (owned[0] if owned else None)
        except ValueError:
            pid = None
        if not pid or pid not in owned:
            return None
        return q1(conn, "SELECT * FROM players WHERE id=?", (pid,))
    return None


@bp.route("/me/assignments", methods=["GET"])
@login_required
def my_assignments():
    conn = get_conn()
    player = _resolve_player_for_view(conn)
    if not player:
        conn.close()
        abort(403)
    items = asg.player_assignments_with_status(conn, player["id"], player["group_id"]) if player["group_id"] else []
    conn.close()
    can_submit = g.user["role"] == "PLAYER"
    return render_template("player_assignments.html", assignments=items, player=player, can_submit=can_submit)


@bp.route("/me/assignments/<int:assignment_id>/submit", methods=["POST"])
@login_required
def submit(assignment_id):
    if g.user["role"] != "PLAYER":
        abort(403)
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE user_id=?", (g.user["id"],))
    if not player:
        conn.close()
        abort(403)
    assignment = asg.get_assignment(conn, assignment_id)
    if not assignment or assignment["group_id"] != player["group_id"]:
        conn.close()
        abort(404)
    try:
        file_url, file_type = _save_submission_file(request.files.get("file"))
        asg.submit_assignment(conn, assignment_id, player["id"], file_url, file_type, g.user["id"])
        conn.commit()
        flash("تم تسليم المهمة بنجاح ✅ بانتظار مراجعة المدرب")
    except asg.AssignmentError as e:
        flash(str(e))
    conn.close()
    return redirect("/me/assignments")
