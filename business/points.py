"""رصيد فوق! (FOUQ Points) wallet & ledger. Balance is always a derived
sum of transactions - never edited directly - and never goes negative."""
from db import q, q1, ex
from business.audit import log as audit_log


class PointsError(Exception):
    pass


# Preset quick-action reasons (رصيد فوق) — shown as one-tap buttons for
# coaches/supervisors, exactly matching the academy's approved wording.
ADD_REASONS = [
    ("الحضور المبكر", "ATTENDANCE"),
    ("الانضباط", "DISCIPLINE"),
    ("الروح الرياضية", "BEHAVIOR"),
    ("مساعدة زميل", "BEHAVIOR"),
    ("الفوز بتحدٍ", "CHALLENGE"),
    ("تطور ملحوظ", "DEVELOPMENT"),
    ("لاعب الحصة", "ACHIEVEMENT"),
    ("الالتزام باللباس", "DISCIPLINE"),
]

SUBTRACT_REASONS = [
    ("التأخر", "DISCIPLINE"),
    ("سوء السلوك", "BEHAVIOR"),
    ("السب", "BEHAVIOR"),
    ("عدم الالتزام", "DISCIPLINE"),
    ("إفساد التدريب", "DISCIPLINE"),
    ("مخالفة تعليمات المدرب", "DISCIPLINE"),
]


def get_balance(conn, player_id):
    row = q1(conn, "SELECT balance FROM points_wallets WHERE player_id=?", (player_id,))
    if not row:
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (player_id,))
        return 0
    return row["balance"]


def award_points(conn, player_id, amount, reason, category, user_id, training_session_id=None, note=None):
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
              balance_before, balance_after, training_session_id, note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (player_id, amount, reason, category, user_id, before, after, training_session_id, note),
    )
    audit_log(conn, user_id, "POINTS_TRANSACTION", "players", player_id,
              after={"amount": amount, "category": category, "balance_after": after}, reason=reason)
    return after


def coach_points_granted_today(conn, coach_user_id, player_id, cap_default=20):
    row = q1(
        conn,
        """SELECT COALESCE(SUM(amount),0) as total FROM points_transactions
           WHERE user_id=? AND player_id=? AND date(created_at,'+3 hours')=date('now','+3 hours') AND amount > 0""",
        (coach_user_id, player_id),
    )
    return row["total"] if row else 0


def get_history(conn, player_id, limit=100):
    return q(
        conn,
        """SELECT pt.*, u.name as coach_name FROM points_transactions pt
           LEFT JOIN users u ON u.id = pt.user_id
           WHERE pt.player_id=? ORDER BY pt.id DESC LIMIT ?""",
        (player_id, limit),
    )


def cancel_points_transaction(conn, txn_id, user_id):
    """Reverse a points transaction by posting the exact opposite amount as a
    new REVERSAL entry. The original row is never edited/deleted — the ledger
    stays a complete, honest history — and award_points() already guarantees
    the balance can never go negative."""
    txn = q1(conn, "SELECT * FROM points_transactions WHERE id=?", (txn_id,))
    if not txn:
        raise PointsError("العملية غير موجودة")
    if txn["category"] == "REVERSAL":
        raise PointsError("لا يمكن إلغاء عملية إلغاء")
    already = q1(conn, "SELECT id FROM points_transactions WHERE category='REVERSAL' AND reason LIKE ?",
                 (f"%#{txn_id}%",))
    if already:
        raise PointsError("تم إلغاء هذه العملية مسبقًا")
    return award_points(conn, txn["player_id"], -txn["amount"], f"إلغاء عملية #{txn_id}: {txn['reason']}",
                         "REVERSAL", user_id)


# ---------------------------------------------------------------------------
# نقاط التحضير التلقائي (شاشة TV): مبكر / متأخر
# ---------------------------------------------------------------------------
EARLY_REASON = "الحضور المبكر"
LATE_REASON = "الحضور المتأخر"


def attendance_points_awarded(conn, player_id, training_session_id):
    """هل مُنح اللاعب نقاط تحضير تلقائية (مبكر/متأخر) في هذه الحصة؟"""
    return q1(
        conn,
        """SELECT * FROM points_transactions
           WHERE player_id=? AND training_session_id=? AND category='ATTENDANCE'
             AND reason IN (?,?) AND amount > 0 ORDER BY id DESC LIMIT 1""",
        (player_id, training_session_id, EARLY_REASON, LATE_REASON),
    )


def reverse_attendance_points(conn, player_id, training_session_id, user_id):
    """يعكس نقاط التحضير التلقائية لهذه الحصة (إن وُجدت ولم تُعكس) عبر سجل
    عكسي — لا يُحذف أي سجل. إذا كان اللاعب صرف النقاط فعلًا ولا يكفي رصيده
    للعكس يُترك كما هو (الرصيد لا يصبح سالبًا أبدًا)."""
    txn = attendance_points_awarded(conn, player_id, training_session_id)
    if not txn:
        return False
    try:
        cancel_points_transaction(conn, txn["id"], user_id)
        return True
    except PointsError:
        return False
