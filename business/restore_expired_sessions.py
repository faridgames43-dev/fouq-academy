"""One-off admin correction: restore a flat number of sessions to players
whose session-entitlement batches (almost always COMPENSATION credits —
the only type that normally carries its own expires_at) have just expired.

Rules, matching exactly what the academy admin asked for:

  1. Strictly additive. This module NEVER removes or touches anyone's
     existing balance — the only mutation it performs is a brand-new
     grant_entitlement() call per selected player. Sessions are only ever
     deducted through real attendance (business/entitlements.consume_one_session),
     which this file never calls.
  2. The new grant carries expires_at=None, so the restored balance never
     expires again — it sits in the player's account until it's actually
     used or an admin later adjusts it by hand.
  3. Nothing is written until a human reviews the exact list of players
     and confirms — same mandatory preview-then-confirm pattern as
     business/bulk_activation.py, because this still touches every
     selected player's real session balance.

"Expired today" is inherently fuzzy (sync_expirations() flips a batch the
moment anyone loads a page that touches that player, so the exact moment
a batch flips ACTIVE->EXPIRED isn't a fixed daily cutoff). Rather than
guess a single hard rule, compute_plan() surfaces EVERY player with a
currently-EXPIRED batch that actually had unused sessions on it, sorted by
how recently it expired, and pre-selects only the ones that expired in the
last couple of days. The admin reviewing the preview makes the final call
by (un)checking rows before confirming.
"""
from datetime import date
from db import q, ex
from business.entitlements import sync_expirations, grant_entitlement, get_balances

RESTORE_QTY_DEFAULT = 2
RECENT_WINDOW_DAYS = 2  # pre-check batches that expired within the last N days


def compute_plan(conn):
    """Read-only. Returns a list of one dict per player who currently has
    at least one EXPIRED entitlement batch that had real sessions on it
    (quantity_total > 0) — i.e. players who actually lost something, not
    batches that were simply fully used up before expiring. No writes."""
    sync_expirations(conn)  # make sure ACTIVE-but-actually-expired batches are flipped first

    rows = q(
        conn,
        """SELECT se.player_id, se.type, se.expires_at, se.quantity_total,
                  p.first_name, p.last_name, p.player_code, p.player_type
           FROM session_entitlements se
           JOIN players p ON p.id = se.player_id
           WHERE se.status='EXPIRED' AND se.expires_at IS NOT NULL AND se.quantity_total > 0
           ORDER BY se.expires_at DESC, se.player_id""",
    )

    today = date.today()
    by_player = {}
    for r in rows:
        pid = r["player_id"]
        entry = by_player.setdefault(pid, {
            "player_id": pid,
            "name": f"{r['first_name']} {r['last_name']}",
            "player_code": r["player_code"],
            "player_type": r["player_type"],
            "last_expired_at": r["expires_at"],
            "lost_qty": 0,
            "batches": 0,
        })
        if r["expires_at"] > entry["last_expired_at"]:
            entry["last_expired_at"] = r["expires_at"]
        entry["lost_qty"] += r["quantity_total"]
        entry["batches"] += 1

    plan = []
    for entry in by_player.values():
        try:
            exp_date = date.fromisoformat(entry["last_expired_at"])
            days_ago = (today - exp_date).days
        except Exception:
            days_ago = None
        entry["days_ago"] = days_ago
        entry["default_selected"] = days_ago is not None and 0 <= days_ago <= RECENT_WINDOW_DAYS
        entry["current_balance"] = get_balances(conn, entry["player_id"])["TOTAL"]
        plan.append(entry)

    plan.sort(key=lambda e: (e["days_ago"] if e["days_ago"] is not None else 9999, e["player_id"]))
    return plan


def execute_plan(conn, player_ids, admin_user_id, qty=RESTORE_QTY_DEFAULT):
    """Grants exactly `qty` open-ended BONUS sessions to each player_id.
    Purely additive — never touches any existing batch or balance, and
    never expires (expires_at is never passed, so it defaults to None)."""
    restored = 0
    for pid in player_ids:
        grant_entitlement(
            conn, pid, "BONUS", qty,
            expires_at=None,
            reason_code="ADMIN_DECISION",
            reason_text="استرجاع إداري لحصص بعد انتهاء صلاحية الحصص السابقة — رصيد جديد بدون تاريخ انتهاء",
            user_id=admin_user_id,
        )
        restored += 1
    return {"restored": restored, "qty_each": qty}
