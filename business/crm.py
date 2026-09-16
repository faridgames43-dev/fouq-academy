"""Leads CRM + Trial management pipeline."""
from db import q, q1, ex
from business.audit import log as audit_log


def create_lead(conn, parent_name, child_name, child_age, phone, source, campaign, owner_id, branch_id, notes=None):
    lid = ex(
        conn,
        """INSERT INTO leads(parent_name, child_name, child_age, phone, source, campaign, owner_id, branch_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (parent_name, child_name, child_age, phone, source, campaign, owner_id, branch_id, notes),
    )
    audit_log(conn, owner_id, "CREATE_LEAD", "leads", lid, after={"child_name": child_name})
    return lid


def update_lead_status(conn, lead_id, status, user_id, note=None, next_follow_up=None):
    ex(conn, """UPDATE leads SET status=?, last_contact=datetime('now'), notes=COALESCE(?, notes),
                next_follow_up=COALESCE(?, next_follow_up) WHERE id=?""",
       (status, note, next_follow_up, lead_id))
    audit_log(conn, user_id, "UPDATE_LEAD_STATUS", "leads", lead_id, after={"status": status}, reason=note)


def book_trial(conn, lead_id, trial_date, group_id, user_id):
    tid = ex(conn, "INSERT INTO trials(lead_id, trial_date, group_id) VALUES (?,?,?)", (lead_id, trial_date, group_id))
    update_lead_status(conn, lead_id, "TRIAL_BOOKED", user_id)
    return tid


def record_trial_result(conn, trial_id, attended, assessment_notes, recommendation, user_id):
    trial = q1(conn, "SELECT * FROM trials WHERE id=?", (trial_id,))
    ex(conn, "UPDATE trials SET attended=?, assessment_notes=?, recommendation=? WHERE id=?",
       (1 if attended else 0, assessment_notes, recommendation, trial_id))
    new_status = "TRIAL_ATTENDED" if attended else "NO_SHOW"
    update_lead_status(conn, trial["lead_id"], new_status, user_id, note=assessment_notes)


def convert_lead_to_player(conn, trial_id, player_id, user_id):
    trial = q1(conn, "SELECT * FROM trials WHERE id=?", (trial_id,))
    ex(conn, "UPDATE trials SET converted_player_id=? WHERE id=?", (player_id, trial_id))
    update_lead_status(conn, trial["lead_id"], "PAID", user_id)
    audit_log(conn, user_id, "CONVERT_LEAD", "trials", trial_id, after={"player_id": player_id})


def create_referral(conn, referrer_player_id, referred_name, referred_phone, user_id=None):
    return ex(conn, "INSERT INTO referrals(referrer_player_id, referred_name, referred_phone) VALUES (?,?,?)",
              (referrer_player_id, referred_name, referred_phone))


def qualify_referral(conn, referral_id, resulting_player_id, user_id):
    ex(conn, "UPDATE referrals SET status='QUALIFIED', resulting_player_id=? WHERE id=?",
       (resulting_player_id, referral_id))


def reward_referral(conn, referral_id, points, user_id):
    from business.points import award_points
    ref = q1(conn, "SELECT * FROM referrals WHERE id=?", (referral_id,))
    award_points(conn, ref["referrer_player_id"], points, "مكافأة إحالة لاعب جديد", "REFERRAL", user_id)
    ex(conn, "UPDATE referrals SET status='REWARDED' WHERE id=?", (referral_id,))
