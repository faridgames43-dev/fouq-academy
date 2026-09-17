import os
import uuid
from flask import Blueprint, render_template, request, redirect, g, flash, Response, abort
from datetime import date
from db import get_conn, q, q1, ex
from business.rbac import login_required, permission_required, branch_scope, coach_group_ids, roles_required
from business.subscriptions import get_attendance_eligibility, get_latest_subscription, sync_subscription_statuses
from business.entitlements import get_balances, sync_expirations, grant_entitlement
from business.levels import current_level, promotion_readiness
from business.points import get_balance as points_balance, get_history as points_history
from business.assessments import player_development_timeline, child_label
from business.renewal_risk import compute_risk
from business.rewards import player_redemptions
from business.barcode import render_code39
from business.audit import log as audit_log

bp = Blueprint("players", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads", "players")


def _save_player_photo(file_storage, player_code):
    """Resize/save an uploaded player photo. Returns the public URL path or None."""
    if not file_storage or not file_storage.filename:
        return None
    try:
        from PIL import Image
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        fname = f"{player_code}-{uuid.uuid4().hex[:8]}.jpg"
        out_path = os.path.join(UPLOAD_DIR, fname)
        img = Image.open(file_storage.stream)
        img = img.convert("RGB")
        img.thumbnail((500, 500))
        img.save(out_path, "JPEG", quality=85)
        return f"/static/uploads/players/{fname}"
    except Exception:
        return None


def _scoped_player_query(base_sql, params, conn):
    if g.user["role"] == "COACH":
        gids = coach_group_ids(conn, g.user["id"])
        if not gids:
            return base_sql + " AND 1=0", params
        placeholders = ",".join(["?"] * len(gids))
        return base_sql + f" AND p.group_id IN ({placeholders})", params + gids
    branch_id = branch_scope(g.user)
    if branch_id:
        return base_sql + " AND p.branch_id=?", params + [branch_id]
    return base_sql, params


@bp.route("/players")
@login_required
def list_players():
    conn = get_conn()
    sync_subscription_statuses(conn)
    sync_expirations(conn)
    sql = """SELECT p.*, c.name as category_name, gr.name as group_name, co.name as coach_name,
                    b.name as branch_name
             FROM players p LEFT JOIN categories c ON c.id=p.category_id
             LEFT JOIN groups_ gr ON gr.id=p.group_id LEFT JOIN coaches co ON co.id=p.coach_id
             LEFT JOIN branches b ON b.id=p.branch_id WHERE 1=1"""
    params = []
    sql, params = _scoped_player_query(sql, params, conn)

    group_filter = request.args.get("group_id")
    status_filter = request.args.get("status")
    if group_filter:
        sql += " AND p.group_id=?"; params.append(group_filter)
    if status_filter:
        sql += " AND p.status=?"; params.append(status_filter)
    sql += " ORDER BY p.id"
    players = q(conn, sql, tuple(params))

    for p in players:
        elig = get_attendance_eligibility(conn, p["id"])
        p["eligibility"] = elig
        p["balances"] = get_balances(conn, p["id"])
        sub = get_latest_subscription(conn, p["id"])
        p["sub_status"] = sub["status"] if sub else "NO_SUB"

    groups = q(conn, "SELECT * FROM groups_ ORDER BY name")
    conn.close()
    return render_template("players_list.html", players=players, groups=groups,
                            group_filter=group_filter, status_filter=status_filter)


@bp.route("/players/new", methods=["GET", "POST"])
@permission_required("manage_players")
def new_player():
    conn = get_conn()
    if request.method == "POST":
        f = request.form
        code = f.get("player_code") or f"FOUQ-{9000 + (q1(conn, 'SELECT COUNT(*) c FROM players')['c'] + 1)}"
        parent_id = f.get("parent_id") or None
        if not parent_id and f.get("parent_name"):
            from werkzeug.security import generate_password_hash
            uid = ex(conn, "INSERT INTO users(name,phone,password_hash,role) VALUES (?,?,?,?)",
                      (f.get("parent_name"), f.get("parent_phone"), generate_password_hash("Fouq@2026"), "PARENT"))
            parent_id = ex(conn, "INSERT INTO parents(user_id, name, phone) VALUES (?,?,?)",
                           (uid, f.get("parent_name"), f.get("parent_phone")))
        photo_url = _save_player_photo(request.files.get("photo"), code)
        pid = ex(conn, """INSERT INTO players(player_code, first_name, last_name, photo_url, dob, gender, category_id,
                          branch_id, group_id, coach_id, player_type, join_date, referral_code, onboarding_json)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (code, f.get("first_name"), f.get("last_name"), photo_url, f.get("dob"), f.get("gender", "M"),
                  f.get("category_id"), f.get("branch_id"), f.get("group_id") or None, f.get("coach_id") or None,
                  f.get("player_type", "FOUQ"), date.today().isoformat(), f"REF-{code}",
                  '{"account_created": true, "parent_linked": true, "group_assigned": true}'))
        if parent_id:
            ex(conn, "INSERT INTO parent_players(parent_id, player_id) VALUES (?,?)", (parent_id, pid))
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (pid,))
        try:
            previous_sessions = int(f.get("previous_sessions") or 0)
        except ValueError:
            previous_sessions = 0
        if previous_sessions > 0:
            grant_entitlement(conn, pid, "LEGACY", previous_sessions, reason_code="ADMIN_DECISION",
                               reason_text="حصص سابقة عند التسجيل", user_id=g.user["id"])
        audit_log(conn, g.user["id"], "CREATE_PLAYER", "players", pid, after={k: v for k, v in f.items()})
        conn.commit()
        conn.close()
        flash("تم إنشاء اللاعب بنجاح")
        return redirect(f"/players/{pid}")

    branches = q(conn, "SELECT * FROM branches WHERE active=1")
    categories = q(conn, "SELECT * FROM categories")
    groups = q(conn, "SELECT * FROM groups_")
    coaches = q(conn, "SELECT * FROM coaches WHERE active=1")
    parents = q(conn, "SELECT * FROM parents ORDER BY name")
    conn.close()
    return render_template("player_form.html", branches=branches, categories=categories, groups=groups,
                            coaches=coaches, parents=parents)


@bp.route("/players/<int:player_id>")
@login_required
def profile(player_id):
    conn = get_conn()
    player = q1(conn, """SELECT p.*, c.name as category_name, gr.name as group_name, co.name as coach_name,
                         b.name as branch_name FROM players p LEFT JOIN categories c ON c.id=p.category_id
                         LEFT JOIN groups_ gr ON gr.id=p.group_id LEFT JOIN coaches co ON co.id=p.coach_id
                         LEFT JOIN branches b ON b.id=p.branch_id WHERE p.id=?""", (player_id,))
    if not player:
        abort(404)

    sync_subscription_statuses(conn, player_id)
    sync_expirations(conn, player_id)
    balances = get_balances(conn, player_id)
    eligibility = get_attendance_eligibility(conn, player_id)
    subs_history = q(conn, "SELECT * FROM subscriptions WHERE player_id=? ORDER BY id DESC", (player_id,))
    ledger = q(conn, "SELECT * FROM session_ledger WHERE player_id=? ORDER BY id DESC LIMIT 40", (player_id,))
    attendance_hist = q(conn, """SELECT a.*, ts.session_date, ts.start_time, g.name as group_name
                                 FROM attendance a JOIN training_sessions ts ON ts.id=a.training_session_id
                                 JOIN groups_ g ON g.id=ts.group_id WHERE a.player_id=?
                                 ORDER BY ts.session_date DESC LIMIT 30""", (player_id,))
    total_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=?", (player_id,))["c"]
    present_sessions = q1(conn, "SELECT COUNT(*) c FROM attendance WHERE player_id=? AND status IN ('PRESENT','LATE')",
                           (player_id,))["c"]
    attendance_pct = round(present_sessions / total_sessions * 100, 1) if total_sessions else 0

    dev_timeline = player_development_timeline(conn, player_id)
    level = current_level(conn, player_id)
    readiness = promotion_readiness(conn, player_id)
    pts_balance = points_balance(conn, player_id)
    pts_hist = points_history(conn, player_id, 20)
    achievements = q(conn, """SELECT a.*, pa.earned_at FROM player_achievements pa
                              JOIN achievements a ON a.id=pa.achievement_id WHERE pa.player_id=?
                              ORDER BY pa.earned_at DESC""", (player_id,))
    redemptions = player_redemptions(conn, player_id)
    risk = compute_risk(conn, player_id)
    notes = q(conn, "SELECT * FROM renewal_notes WHERE player_id=? ORDER BY id DESC LIMIT 10", (player_id,))
    parent = q1(conn, """SELECT pr.* FROM parents pr JOIN parent_players pp ON pp.parent_id=pr.id
                         WHERE pp.player_id=?""", (player_id,))
    onboarding = {}
    if player.get("onboarding_json"):
        import json
        try:
            onboarding = json.loads(player["onboarding_json"])
        except Exception:
            onboarding = {}
    onboarding_pct = round(sum(1 for v in onboarding.values() if v) / len(onboarding) * 100) if onboarding else 0

    conn.close()
    return render_template("player_profile.html", player=player, balances=balances, eligibility=eligibility,
                            subs_history=subs_history, ledger=ledger, attendance_hist=attendance_hist,
                            attendance_pct=attendance_pct, dev_timeline=dev_timeline, level=level,
                            readiness=readiness, pts_balance=pts_balance, pts_hist=pts_hist,
                            achievements=achievements, redemptions=redemptions, risk=risk, notes=notes,
                            parent=parent, child_label=child_label, onboarding=onboarding, onboarding_pct=onboarding_pct)


@bp.route("/players/<int:player_id>/barcode.png")
@login_required
def barcode(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT player_code FROM players WHERE id=?", (player_id,))
    conn.close()
    if not player:
        abort(404)
    png = render_code39(player["player_code"])
    return Response(png, mimetype="image/png")


@bp.route("/players/<int:player_id>/override", methods=["POST"])
@permission_required("administrative_override")
def toggle_override(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    new_val = 0 if player["attendance_override"] else 1
    note = request.form.get("note", "سمحت الإدارة بالحضور استثنائيًا")
    ex(conn, "UPDATE players SET attendance_override=?, attendance_override_note=?, attendance_override_by=? WHERE id=?",
       (new_val, note if new_val else None, g.user["id"] if new_val else None, player_id))
    audit_log(conn, g.user["id"], "TOGGLE_ADMIN_OVERRIDE", "players", player_id, after={"override": new_val}, reason=note)
    conn.commit()
    conn.close()
    flash("تم تحديث الصلاحية الإدارية")
    return redirect(f"/players/{player_id}")


@bp.route("/players/<int:player_id>/note", methods=["POST"])
@permission_required("manage_players")
def add_note(player_id):
    conn = get_conn()
    ex(conn, "UPDATE players SET notes=? WHERE id=?", (request.form.get("notes", ""), player_id))
    audit_log(conn, g.user["id"], "UPDATE_PLAYER_NOTE", "players", player_id)
    conn.commit()
    conn.close()
    flash("تم حفظ الملاحظة")
    return redirect(f"/players/{player_id}")


@bp.route("/players/<int:player_id>/photo", methods=["POST"])
@permission_required("manage_players")
def update_photo(player_id):
    conn = get_conn()
    player = q1(conn, "SELECT player_code FROM players WHERE id=?", (player_id,))
    if not player:
        conn.close()
        abort(404)
    photo_url = _save_player_photo(request.files.get("photo"), player["player_code"])
    if photo_url:
        ex(conn, "UPDATE players SET photo_url=? WHERE id=?", (photo_url, player_id))
        audit_log(conn, g.user["id"], "UPDATE_PLAYER_PHOTO", "players", player_id, after={"photo_url": photo_url})
        conn.commit()
        flash("تم تحديث صورة اللاعب")
    else:
        flash("تعذّر رفع الصورة — تأكد من أنها صورة صالحة (jpg/png)")
    conn.close()
    return redirect(f"/players/{player_id}")
