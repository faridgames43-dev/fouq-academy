"""One-time "clean slate" maintenance action: wipes every PLAYER, COACH and
PARENT record (and everything that hangs off them — attendance, points,
achievements, subscriptions, assignment submissions, etc.) so the academy
can start onboarding *real* students/coaches with a completely empty list.

Deliberately narrow: never touches branches, categories, groups, packages,
levels, achievements/challenges catalogs, rewards catalog, leads/CRM, or any
SUPER_ADMIN / PROJECT_MANAGER / BRANCH_MANAGER / SUPERVISOR staff account —
those are the academy's own structure/config and the people who run it, not
"test data".

Always call `build_backup()` first and hand the result to the admin as a
downloadable JSON file *before* calling `execute_reset()` — this is
irreversible (SQLite FK cascade, no undo) so the backup is the only safety
net.
"""
import json
from db import q, q1, ex
from business.audit import log as audit_log


def counts(conn):
    """Quick summary for the confirmation screen: how many rows this would touch."""
    return {
        "players": q1(conn, "SELECT COUNT(*) c FROM players")["c"],
        "coaches": q1(conn, "SELECT COUNT(*) c FROM coaches")["c"],
        "parents": q1(conn, "SELECT COUNT(*) c FROM parents")["c"],
        "training_sessions": q1(conn, "SELECT COUNT(*) c FROM training_sessions")["c"],
    }


def build_backup(conn):
    """Full snapshot of every row that execute_reset() is about to delete,
    as plain JSON-able dicts — the safety net before an irreversible wipe."""
    tables = [
        "players", "coaches", "parents", "parent_players", "attendance",
        "training_sessions", "subscriptions", "session_entitlements", "session_ledger",
        "assessments", "assessment_scores", "player_levels", "points_wallets",
        "points_transactions", "player_achievements", "player_challenges",
        "assignment_submissions", "reward_redemptions", "referrals",
    ]
    backup = {"generated_note": "نسخة احتياطية كاملة قبل حذف بيانات اللاعبين/المدربين/أولياء الأمور التجريبية"}
    for t in tables:
        backup[t] = q(conn, f"SELECT * FROM {t}")
    # also the login accounts (role) being removed, so nothing is lost
    backup["users_players_coaches_parents"] = q(
        conn, "SELECT * FROM users WHERE role IN ('PLAYER','COACH','PARENT')"
    )
    return backup


def execute_reset(conn, admin_user_id):
    """Deletes, in FK-safe child-before-parent order, every player/coach/parent
    and everything that references them. Wrapped by the caller in a single
    connection/commit so it is all-or-nothing."""
    player_ids = [r["id"] for r in q(conn, "SELECT id FROM players")]
    coach_ids = [r["id"] for r in q(conn, "SELECT id FROM coaches")]
    parent_ids = [r["id"] for r in q(conn, "SELECT id FROM parents")]
    player_user_ids = [r["user_id"] for r in q(conn, "SELECT user_id FROM players WHERE user_id IS NOT NULL")]
    coach_user_ids = [r["user_id"] for r in q(conn, "SELECT user_id FROM coaches WHERE user_id IS NOT NULL")]
    parent_user_ids = [r["user_id"] for r in q(conn, "SELECT user_id FROM parents WHERE user_id IS NOT NULL")]

    summary = {
        "players": len(player_ids), "coaches": len(coach_ids), "parents": len(parent_ids),
    }

    # A few tables keep a *history* row that must survive even though the
    # person who made it is gone (audit trail, a task a coach once created,
    # a lead a coach once owned) — detach the reference (SET NULL) instead
    # of deleting the row itself, so nothing but the demo people disappears.
    removed_user_ids = player_user_ids + coach_user_ids + parent_user_ids
    if removed_user_ids:
        placeholders = ",".join("?" * len(removed_user_ids))
        ex(conn, f"UPDATE audit_logs SET user_id=NULL WHERE user_id IN ({placeholders})", tuple(removed_user_ids))
        ex(conn, f"UPDATE assignments SET created_by=NULL WHERE created_by IN ({placeholders})", tuple(removed_user_ids))
        ex(conn, f"UPDATE leads SET owner_id=NULL WHERE owner_id IN ({placeholders})", tuple(removed_user_ids))

    # children of players, deepest first
    ex(conn, "DELETE FROM assessment_scores WHERE assessment_id IN (SELECT id FROM assessments WHERE player_id IN (SELECT id FROM players))")
    ex(conn, "DELETE FROM assessments WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM reward_redemptions WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM points_transactions WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM points_wallets WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM player_achievements WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM player_challenges WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM player_levels WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM assignment_submissions WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM attendance WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM session_ledger WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM session_entitlements WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM subscriptions WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM renewal_notes WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM referrals WHERE referrer_player_id IN (SELECT id FROM players) OR resulting_player_id IN (SELECT id FROM players)")
    ex(conn, "UPDATE trials SET converted_player_id=NULL WHERE converted_player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM notifications WHERE player_id IN (SELECT id FROM players)")
    ex(conn, "DELETE FROM parent_players")

    # trainng sessions (demo schedule) — attendance already gone above
    ex(conn, "DELETE FROM training_sessions")

    # players themselves, then their login accounts
    ex(conn, "DELETE FROM players")
    if player_user_ids:
        placeholders = ",".join("?" * len(player_user_ids))
        ex(conn, f"DELETE FROM permissions_overrides WHERE user_id IN ({placeholders})", tuple(player_user_ids))
        ex(conn, f"DELETE FROM notifications WHERE user_id IN ({placeholders})", tuple(player_user_ids))
        ex(conn, f"DELETE FROM users WHERE id IN ({placeholders})", tuple(player_user_ids))

    # parents, then their login accounts
    ex(conn, "DELETE FROM parents")
    if parent_user_ids:
        placeholders = ",".join("?" * len(parent_user_ids))
        ex(conn, f"DELETE FROM permissions_overrides WHERE user_id IN ({placeholders})", tuple(parent_user_ids))
        ex(conn, f"DELETE FROM notifications WHERE user_id IN ({placeholders})", tuple(parent_user_ids))
        ex(conn, f"DELETE FROM users WHERE id IN ({placeholders})", tuple(parent_user_ids))

    # groups keep existing (structure), just detach the coach that's about to disappear
    ex(conn, "UPDATE groups_ SET coach_id=NULL")

    # coaches, then their login accounts
    ex(conn, "DELETE FROM coaches")
    if coach_user_ids:
        placeholders = ",".join("?" * len(coach_user_ids))
        ex(conn, f"DELETE FROM permissions_overrides WHERE user_id IN ({placeholders})", tuple(coach_user_ids))
        ex(conn, f"DELETE FROM notifications WHERE user_id IN ({placeholders})", tuple(coach_user_ids))
        ex(conn, f"DELETE FROM users WHERE id IN ({placeholders})", tuple(coach_user_ids))

    audit_log(conn, admin_user_id, "RESET_DEMO_DATA", "system", reason="حذف بيانات اللاعبين/المدربين/أولياء الأمور التجريبية بطلب صريح من الإدارة",
              before=summary)
    return summary
