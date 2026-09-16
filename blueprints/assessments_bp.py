from flask import Blueprint, render_template, request, redirect, g, flash, abort
from db import get_conn, q, q1
from business.rbac import permission_required, coach_group_ids
from business.assessments import create_assessment, get_overall, latest_assessment
from business import achievements as ach

bp = Blueprint("assessments_bp", __name__)


@bp.route("/players/<int:player_id>/assess", methods=["GET", "POST"])
@permission_required("run_assessment")
def full_assessment(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player:
        abort(404)
    categories = q(conn, "SELECT * FROM assessment_categories ORDER BY id")
    metrics = q(conn, "SELECT * FROM assessment_metrics WHERE active=1 ORDER BY category_id, id")
    coach = q1(conn, "SELECT id FROM coaches WHERE user_id=?", (g.user["id"],))

    if request.method == "POST":
        scores = {}
        for m in metrics:
            val = request.form.get(f"metric_{m['id']}")
            if val not in (None, ""):
                scores[m["id"]] = float(val)
        atype = request.form.get("type", "PERIODIC")
        prev = latest_assessment(conn, player_id)
        prev_overall = get_overall(conn, prev["id"])[0] if prev else None
        aid = create_assessment(conn, player_id, atype, coach["id"] if coach else None, scores,
                                 request.form.get("notes"), user_id=g.user["id"])
        new_overall = get_overall(conn, aid)[0]
        ach.check_development_leap(conn, player_id, prev_overall, new_overall, user_id=g.user["id"])
        conn.commit()
        conn.close()
        flash("تم حفظ التقييم بنجاح")
        return redirect(f"/players/{player_id}")

    has_initial = latest_assessment(conn, player_id, "INITIAL") is not None
    conn.close()
    return render_template("assessment_form.html", player=player, categories=categories, metrics=metrics,
                            has_initial=has_initial)


@bp.route("/assessments/quick", methods=["GET", "POST"])
@permission_required("run_assessment")
def quick_assessment():
    conn = get_conn()
    if g.user["role"] == "COACH":
        gids = coach_group_ids(conn, g.user["id"])
        groups = q(conn, "SELECT * FROM groups_ WHERE id IN ({})".format(",".join(["?"] * len(gids)) or "0"), tuple(gids))
    else:
        groups = q(conn, "SELECT * FROM groups_")
    metrics = q(conn, "SELECT m.*, c.name as cat_name FROM assessment_metrics m JOIN assessment_categories c ON c.id=m.category_id ORDER BY c.id")
    coach = q1(conn, "SELECT id FROM coaches WHERE user_id=?", (g.user["id"],))

    selected_group = request.values.get("group_id")
    players = []
    if selected_group:
        players = q(conn, "SELECT * FROM players WHERE group_id=?", (selected_group,))

    if request.method == "POST":
        metric_id = request.form.get("metric_id")
        player_ids = request.form.getlist("player_ids")
        score = request.form.get("score")
        count = 0
        for pid in player_ids:
            aid = create_assessment(conn, pid, "QUICK", coach["id"] if coach else None,
                                     {int(metric_id): float(score)}, "رصد سريع", user_id=g.user["id"])
            count += 1
        conn.commit()
        conn.close()
        flash(f"تم رصد {count} لاعب بنجاح")
        return redirect(f"/assessments/quick?group_id={selected_group}")

    conn.close()
    return render_template("quick_assessment.html", groups=groups, metrics=metrics, players=players,
                            selected_group=selected_group)
