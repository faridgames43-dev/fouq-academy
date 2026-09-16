"""Legacy / Transitional Players from the old Tawasol club: they may use
their remaining pre-paid sessions, but are never auto-enrolled as full
paying FOUQ members - they enter a Conversion Pipeline instead."""
from db import q1, ex
from business.entitlements import get_balances
from business.audit import log as audit_log


def check_conversion_eligibility(conn, player_id, user_id=None):
    player = q1(conn, "SELECT * FROM players WHERE id=?", (player_id,))
    if not player or player["player_type"] != "LEGACY":
        return False
    balances = get_balances(conn, player_id)
    if balances["TOTAL"] <= 0 and player["status"] != "CONVERSION_PENDING":
        ex(conn, "UPDATE players SET status='CONVERSION_PENDING' WHERE id=?", (player_id,))
        audit_log(conn, user_id, "LEGACY_CONVERSION_PENDING", "players", player_id,
                  reason="اكتملت الحصص السابقة - مؤهل للانتقال إلى فوق")
        return True
    return False


def convert_to_fouq(conn, player_id, user_id):
    """Explicit, manual step - never automatic."""
    ex(conn, "UPDATE players SET player_type='FOUQ', status='ACTIVE' WHERE id=?", (player_id,))
    audit_log(conn, user_id, "LEGACY_CONVERTED_TO_FOUQ", "players", player_id)
