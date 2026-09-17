رصيدفوقلايمكنأنيصبحالرصيدسالبًاالعمليةغيرموجودةلايمكنإلغاءعمليةإلغاءتمإلغاءهذهالعمليةمسبقًاإلغاءعملية"""رصيد فوق! (FOUQ Points) wallet & ledger. Balance is always a derived
sum of transactions - never edited directly - and never goes negative."""
from db import q, q1, ex
from business.audit import log as audit_log


class PointsError(Exception):
    pass


def get_balance(conn, player_id):
    row = q1(conn, "SELECT balance FROM points_wallets WHERE player_id=?", (player_id,))
    if not row:
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (player_id,))
        return 0
    return row["balance"]


def award_points(conn, player_id, amount, reason, category, user_id, training_session_id=None):
    if amount == 0:
        return
    before = get_balance(conn, player_id)
    after = before + amount
    if after < 0:
        raise PointsError("لا يمكن أن يصبح الرصيد سالبًا")
    row = q1(conn, "SELECT player_id FROM points_wallets WHERE player_id=?", (player_id,))
    if row:
        ex(conn, "UPDATE points_wallets SET balance=? WHERE player_id=?", (after, player_id))
    else:
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,?)", (player_id, after))
    ex(
        conn,
        """INSERT INTO points_transactions(player_id, amount, reason, category, user_id,
              balance_before, balance_after, training_session_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (player_id, amount, reason, category, user_id, before, after, training_session_id),
    )
    audit_log(conn, user_id, "POINTS_TRANSACTION", "players", player_id,
              after={"amount": amount, "category": category, "balance_after": after}, reason=reason)
    return after


def coach_points_granted_today(conn, coach_user_id, player_id, cap_default=20):
    row = q1(
        conn,
        """SELECT COALESCE(SUM(amount),0) as total FROM points_transactions
           WHERE user_id=? AND player_id=? AND date(created_at)=date('now') AND amount > 0""",
        (coach_user_id, player_id),
    )
    return row["total"] if row else 0


def get_history(conn, player_id, limit=100):
    return q(conn, "SELECT * FROM points_transactions WHERE player_id=? ORDER BY id DESC LIMIT ?",
              (player_id, limit))
