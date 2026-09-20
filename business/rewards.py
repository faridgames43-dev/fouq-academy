"""متجر فوق (Reward Store). Points & stock are reserved at request time and
correctly restored on cancellation (Transaction Reversal), never fudged."""
from db import q, q1, ex
from business.points import award_points, get_balance, PointsError
from business.audit import log as audit_log


class RewardError(Exception):
    pass


def list_rewards(conn, active_only=True):
    sql = "SELECT * FROM rewards"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY cost ASC"
    return q(conn, sql)


def get_reward(conn, reward_id):
    return q1(conn, "SELECT * FROM rewards WHERE id=?", (reward_id,))


def create_reward(conn, name, description, cost, stock, photo_url, user_id):
    reward_id = ex(conn, """INSERT INTO rewards(name, description, photo_url, cost, stock, active)
                            VALUES (?,?,?,?,?,1)""", (name, description, photo_url, cost, stock))
    audit_log(conn, user_id, "CREATE_REWARD", "rewards", reward_id,
              after={"name": name, "cost": cost, "stock": stock})
    return reward_id


def update_reward(conn, reward_id, name, description, cost, stock, active, photo_url, user_id):
    before = q1(conn, "SELECT * FROM rewards WHERE id=?", (reward_id,))
    if not before:
        raise RewardError("المنتج غير موجود")
    if photo_url:
        ex(conn, """UPDATE rewards SET name=?, description=?, cost=?, stock=?, active=?, photo_url=? WHERE id=?""",
           (name, description, cost, stock, active, photo_url, reward_id))
    else:
        ex(conn, """UPDATE rewards SET name=?, description=?, cost=?, stock=?, active=? WHERE id=?""",
           (name, description, cost, stock, active, reward_id))
    audit_log(conn, user_id, "UPDATE_REWARD", "rewards", reward_id,
              before={"name": before["name"], "cost": before["cost"]},
              after={"name": name, "cost": cost, "stock": stock})


def request_redemption(conn, player_id, reward_id, user_id):
    reward = q1(conn, "SELECT * FROM rewards WHERE id=?", (reward_id,))
    if not reward or not reward["active"]:
        raise RewardError("الجائزة غير متاحة")
    if reward["stock"] <= 0:
        raise RewardError("المخزون غير متوفر لهذه الجائزة")
    balance = get_balance(conn, player_id)
    if balance < reward["cost"]:
        raise RewardError("رصيد فوق غير كافٍ لاستبدال هذه الجائزة")

    new_balance = award_points(conn, player_id, -reward["cost"], f"استبدال جائزة: {reward['name']}",
                                "REDEMPTION", user_id)
    ex(conn, "UPDATE rewards SET stock = stock - 1 WHERE id=?", (reward_id,))
    redemption_id = ex(
        conn,
        """INSERT INTO reward_redemptions(player_id, reward_id, status) VALUES (?,?,'PENDING')""",
        (player_id, reward_id),
    )
    ptx = q1(conn, "SELECT id FROM points_transactions WHERE player_id=? ORDER BY id DESC LIMIT 1", (player_id,))
    if ptx:
        ex(conn, "UPDATE reward_redemptions SET points_transaction_id=? WHERE id=?", (ptx["id"], redemption_id))
    audit_log(conn, user_id, "REQUEST_REDEMPTION", "reward_redemptions", redemption_id,
              after={"player_id": player_id, "reward": reward["name"], "cost": reward["cost"]})
    return redemption_id


def update_redemption_status(conn, redemption_id, new_status, user_id):
    red = q1(conn, "SELECT * FROM reward_redemptions WHERE id=?", (redemption_id,))
    if not red:
        raise RewardError("الطلب غير موجود")
    if new_status == "CANCELLED" and red["status"] != "CANCELLED":
        reward = q1(conn, "SELECT * FROM rewards WHERE id=?", (red["reward_id"],))
        award_points(conn, red["player_id"], reward["cost"], f"إلغاء استبدال: {reward['name']}",
                      "REDEMPTION", user_id)
        ex(conn, "UPDATE rewards SET stock = stock + 1 WHERE id=?", (red["reward_id"],))
    ex(conn, "UPDATE reward_redemptions SET status=?, decided_by=?, decided_at=datetime('now') WHERE id=?",
       (new_status, user_id, redemption_id))
    audit_log(conn, user_id, "UPDATE_REDEMPTION", "reward_redemptions", redemption_id,
              before={"status": red["status"]}, after={"status": new_status})


def nearest_reward(conn, balance):
    """The cheapest reward the player can't yet afford — used to show
    'X points to go' on the player/parent home screens."""
    return q1(conn, "SELECT * FROM rewards WHERE active=1 AND stock>0 AND cost>? ORDER BY cost ASC LIMIT 1",
              (balance,))


def player_redemptions(conn, player_id):
    return q(
        conn,
        """SELECT rr.*, r.name, r.photo_url, r.cost FROM reward_redemptions rr
           JOIN rewards r ON r.id = rr.reward_id WHERE rr.player_id=? ORDER BY rr.id DESC""",
        (player_id,),
    )
