"""Role-Based Access Control.

Roles: SUPER_ADMIN, PROJECT_MANAGER, BRANCH_MANAGER, SUPERVISOR, COACH, PARENT, PLAYER

Permissions are plain string keys; ROLE_PERMISSIONS is the default matrix
and can be overridden per-user via the permissions_overrides table (making
the system "customizable in the future" as required)."""
from functools import wraps
from flask import session, redirect, url_for, abort, g
from db import q1

ALL_ROLES = ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH", "PARENT", "PLAYER"]

PERMISSIONS = {
    "view_all_branches": ["SUPER_ADMIN", "PROJECT_MANAGER"],
    "manage_branches": ["SUPER_ADMIN"],
    "manage_users": ["SUPER_ADMIN", "PROJECT_MANAGER"],
    "manage_players": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "manage_coaches": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER"],
    "manage_packages": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER"],
    "manage_subscriptions": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "manage_compensation": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "take_attendance": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH"],
    "cancel_attendance": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "administrative_override": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "run_assessment": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH"],
    "approve_promotion": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "grant_points": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH"],
    "manage_assignments": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR", "COACH"],
    "manage_rewards": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "decide_redemption": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "view_reports": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "view_audit_log": ["SUPER_ADMIN", "PROJECT_MANAGER"],
    "manage_settings": ["SUPER_ADMIN", "PROJECT_MANAGER"],
    "manage_crm": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"],
    "view_finance": ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER"],
}

ADMIN_ROLES = ["SUPER_ADMIN", "PROJECT_MANAGER", "BRANCH_MANAGER", "SUPERVISOR"]


def has_permission(conn, user, key):
    if not user:
        return False
    override = q1(conn, "SELECT allowed FROM permissions_overrides WHERE user_id=? AND permission_key=?",
                  (user["id"], key))
    if override is not None:
        return bool(override["allowed"])
    return user["role"] in PERMISSIONS.get(key, [])


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("auth.login"))
            if session.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def permission_required(key):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("auth.login"))
            from db import get_conn
            conn = get_conn()
            user = q1(conn, "SELECT * FROM users WHERE id=?", (session["user_id"],))
            allowed = has_permission(conn, user, key)
            conn.close()
            if not allowed:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def branch_scope(user):
    """None => all branches (Super Admin / Project Manager). Otherwise a branch_id."""
    if user["role"] in ("SUPER_ADMIN", "PROJECT_MANAGER"):
        return None
    return user["branch_id"]


def coach_group_ids(conn, coach_user_id):
    row = q1(conn, "SELECT id FROM coaches WHERE user_id=?", (coach_user_id,))
    if not row:
        return []
    from db import q
    groups = q(conn, "SELECT id FROM groups_ WHERE coach_id=?", (row["id"],))
    return [g["id"] for g in groups]


def parent_player_ids(conn, parent_user_id):
    row = q1(conn, "SELECT id FROM parents WHERE user_id=?", (parent_user_id,))
    if not row:
        return []
    from db import q
    links = q(conn, "SELECT player_id FROM parent_players WHERE parent_id=?", (row["id"],))
    return [l["player_id"] for l in links]
