"""Hard-delete for individual players (or a selected batch of them) from the
"/players" list page — distinct from the one-time whole-database
"بدء من جديد" reset in business/reset_demo.py. This is used ongoing by
admins to remove a specific mistaken/duplicate/test player entry.

Deletes the player and everything that hangs off *that player only*
(attendance, points, achievements, subscriptions, assignment submissions,
etc.) plus their own PLAYER-role login account. Does NOT touch:
  - the parent's account or parent record, even if this was their only
    child — a parent may add another child later, and the account itself
    does no harm sitting unused. (Just the parent_players link row is
    removed so the parent's UI stops showing the deleted child.)
  - coaches, groups, branches, categories, packages — academy structure.
  - audit_logs / assignments / leads history rows that reference the
    player's user_id — those are SET NULL (detached) rather than deleted,
    so the historical record isn't erased, matching reset_demo.py's
    pattern.

Irreversible (SQLite, no undo) — callers must confirm with the admin
before calling delete_players().
"""
from db import q, q1, ex
from business.audit import log as audit_log


def delete_players(conn, player_ids, admin_user_id):
    """Deletes the given players (by id) and everything scoped to them.
    Returns {"deleted": n, "skipped": n, "names": [...]} for a confirmation
    message. Safe to call with an empty/short list — used for both
    "delete selected" and "delete all" (the caller just passes every id)."""
    player_ids = [int(pid) for pid in player_ids]
    if not player_ids:
        return {"deleted": 0, "skipped": 0, "names": []}

    placeholders = ",".join("?" * len(player_ids))
    rows = q(conn, f"SELECT id, first_name, last_name, player_code, user_id FROM players WHERE id IN ({placeholders})",
             tuple(player_ids))
    found_ids = [r["id"] for r in rows]
    names = [f"{r['first_name']} {r['last_name']} ({r['player_code']})" for r in rows]
    user_ids = [r["user_id"] for r in rows if r["user_id"]]
    skipped = len(player_ids) - len(found_ids)
    if not found_ids:
        return {"deleted": 0, "skipped": skipped, "names": []}

    ph = ",".join("?" * len(found_ids))
    pid_tuple = tuple(found_ids)

    # Detach (don't delete) history rows that reference the player's own
    # login account, so audit trail / task authorship survive.
    if user_ids:
        uph = ",".join("?" * len(user_ids))
        ex(conn, f"UPDATE audit_logs SET user_id=NULL WHERE user_id IN ({uph})", tuple(user_ids))
        ex(conn, f"UPDATE assignments SET created_by=NULL WHERE created_by IN ({uph})", tuple(user_ids))
        ex(conn, f"UPDATE leads SET owner_id=NULL WHERE owner_id IN ({uph})", tuple(user_ids))

    # children of these players, deepest first
    ex(conn, f"DELETE FROM assessment_scores WHERE assessment_id IN (SELECT id FROM assessments WHERE player_id IN ({ph}))", pid_tuple)
    ex(conn, f"DELETE FROM assessments WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM reward_redemptions WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM points_transactions WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM points_wallets WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM player_achievements WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM player_challenges WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM player_levels WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM assignment_submissions WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM attendance WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM session_ledger WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM session_entitlements WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM subscriptions WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM renewal_notes WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM referrals WHERE referrer_player_id IN ({ph}) OR resulting_player_id IN ({ph})", pid_tuple + pid_tuple)
    ex(conn, f"UPDATE trials SET converted_player_id=NULL WHERE converted_player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM notifications WHERE player_id IN ({ph})", pid_tuple)
    ex(conn, f"DELETE FROM parent_players WHERE player_id IN ({ph})", pid_tuple)

    # the players themselves, then their own login accounts
    ex(conn, f"DELETE FROM players WHERE id IN ({ph})", pid_tuple)
    if user_ids:
        uph = ",".join("?" * len(user_ids))
        ex(conn, f"DELETE FROM permissions_overrides WHERE user_id IN ({uph})", tuple(user_ids))
        ex(conn, f"DELETE FROM notifications WHERE user_id IN ({uph})", tuple(user_ids))
        ex(conn, f"DELETE FROM users WHERE id IN ({uph})", tuple(user_ids))

    audit_log(conn, admin_user_id, "DELETE_PLAYERS", "players", None,
              before={"player_ids": found_ids, "names": names},
              reason="حذف لاعب/لاعبين من قائمة اللاعبين بطلب صريح من الإدارة")

    return {"deleted": len(found_ids), "skipped": skipped, "names": names}
