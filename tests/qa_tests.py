"""Formal QA suite covering the 18 mandatory test scenarios from the product
spec (section 73). Runs against a disposable COPY of the seeded database so
the demo data shown to the user is never touched by test fixtures.

Run: python3 tests/qa_tests.py
"""
import os
import sys
import shutil
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DB = os.path.join(BASE_DIR, "data", "academy.db")
TMP_DB = os.path.join(tempfile.gettempdir(), "fouq_qa_test.db")

shutil.copyfile(ORIGINAL_DB, TMP_DB)
db_module.DB_PATH = TMP_DB  # redirect all get_conn() calls to the disposable copy

from db import get_conn, q1, q, ex
from business import entitlements as ent
from business import subscriptions as subs
from business import attendance as att
from business import legacy as leg
from business import crm
from business.audit import log as audit_log

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}  {detail}")


def make_test_player(conn, code, category_id=None, branch_id=None, group_id=None, player_type="FOUQ"):
    if category_id is None:
        category_id = q1(conn, "SELECT id FROM categories LIMIT 1")["id"]
    if branch_id is None:
        branch_id = q1(conn, "SELECT id FROM branches WHERE active=1 LIMIT 1")["id"]
    pid = ex(conn, """INSERT INTO players(player_code, first_name, last_name, category_id, branch_id, group_id,
                      player_type, join_date) VALUES (?,?,?,?,?,?,?,?)""",
             (code, "اختبار", code, category_id, branch_id, group_id, player_type, date.today().isoformat()))
    ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (pid,))
    return pid


def make_test_session(conn, group_id=None, days_ago=0):
    if group_id is None:
        group_id = q1(conn, "SELECT id FROM groups_ LIMIT 1")["id"]
    grp = q1(conn, "SELECT * FROM groups_ WHERE id=?", (group_id,))
    sid = ex(conn, """INSERT INTO training_sessions(branch_id, group_id, coach_id, session_date, start_time, end_time)
                      VALUES (?,?,?,?,?,?)""",
             (grp["branch_id"], group_id, grp["coach_id"], (date.today() - timedelta(days=days_ago)).isoformat(),
              "17:00", "18:30"))
    return sid


ADMIN_USER_ID = 2  # pm@fouq.sa from seed


def run():
    conn = get_conn()

    # TEST 1: subscription with 12 sessions, attends, becomes 11
    p = make_test_player(conn, "QA-T1")
    pkg = q1(conn, "SELECT * FROM packages LIMIT 1")
    subs.create_subscription(conn, p, pkg, date.today().isoformat(), 699, 0, 699, "CASH", "QA1", user_id=ADMIN_USER_ID,
                              sessions_count_override=12)
    s1 = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p,))["group_id"])
    att.mark_attendance(conn, s1, p, "PRESENT", ADMIN_USER_ID)
    bal = ent.get_balances(conn, p)
    check("TEST 1: 12 sessions -> attend -> 11 remaining", bal["REGULAR"] == 11, f"got {bal['REGULAR']}")

    # TEST 2: double barcode scan keeps it at 11
    result2 = att.checkin_by_code(conn, s1, "QA-T1", ADMIN_USER_ID)
    bal = ent.get_balances(conn, p)
    check("TEST 2: double scan does not deduct twice", bal["REGULAR"] == 11 and result2["already"] is True,
          f"got {bal['REGULAR']}, already={result2['already']}")

    # TEST 3: cancel attendance restores the correct session
    att_row = q1(conn, "SELECT * FROM attendance WHERE training_session_id=? AND player_id=?", (s1, p))
    att.cancel_attendance(conn, att_row["id"], ADMIN_USER_ID)
    bal = ent.get_balances(conn, p)
    check("TEST 3: cancel attendance restores session", bal["REGULAR"] == 12, f"got {bal['REGULAR']}")

    # TEST 4: subscription expired, no sessions -> Needs Renewal
    p4 = make_test_player(conn, "QA-T4")
    sub4 = subs.create_subscription(conn, p4, pkg, (date.today() - timedelta(days=40)).isoformat(), 699, 0, 699,
                                     "CASH", "QA4", user_id=ADMIN_USER_ID, sessions_count_override=1,
                                     duration_days_override=30)
    s4 = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p4,))["group_id"])
    att.mark_attendance(conn, s4, p4, "PRESENT", ADMIN_USER_ID)  # consume the single session
    elig4 = subs.get_attendance_eligibility(conn, p4)
    check("TEST 4: expired + no sessions -> NO_SESSIONS", elig4["code"] == "NO_SESSIONS", elig4["code"])

    # TEST 5: expired subscription + 3 compensation sessions -> allowed to attend without renewing
    p5 = make_test_player(conn, "QA-T5")
    subs.create_subscription(conn, p5, pkg, (date.today() - timedelta(days=40)).isoformat(), 699, 0, 699, "CASH",
                              "QA5", user_id=ADMIN_USER_ID, sessions_count_override=1, duration_days_override=30)
    s5a = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p5,))["group_id"])
    att.mark_attendance(conn, s5a, p5, "PRESENT", ADMIN_USER_ID)  # burn the regular session first
    ent.grant_entitlement(conn, p5, "COMPENSATION", 3, expires_at=(date.today() + timedelta(days=30)).isoformat(),
                           reason_code="CANCELLED_SESSION", reason_text="QA test grant", user_id=ADMIN_USER_ID)
    elig5 = subs.get_attendance_eligibility(conn, p5)
    check("TEST 5: expired + 3 compensation -> COMPENSATION_ONLY (can attend)",
          elig5["code"] == "COMPENSATION_ONLY", elig5["code"])

    # TEST 6: attends a compensation session -> becomes 2
    s5b = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p5,))["group_id"], days_ago=1)
    att.mark_attendance(conn, s5b, p5, "PRESENT", ADMIN_USER_ID)
    bal5 = ent.get_balances(conn, p5)
    check("TEST 6: attends compensation session -> 2 remaining", bal5["COMPENSATION"] == 2, f"got {bal5['COMPENSATION']}")

    # TEST 7: completes remaining 2 -> 0, status becomes 'needs renewal'
    s5c = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p5,))["group_id"], days_ago=2)
    att.mark_attendance(conn, s5c, p5, "PRESENT", ADMIN_USER_ID)
    s5d = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p5,))["group_id"], days_ago=3)
    att.mark_attendance(conn, s5d, p5, "PRESENT", ADMIN_USER_ID)
    bal5b = ent.get_balances(conn, p5)
    elig5b = subs.get_attendance_eligibility(conn, p5)
    check("TEST 7: compensation reaches 0 -> NO_SESSIONS (needs renewal)",
          bal5b["COMPENSATION"] == 0 and elig5b["code"] == "NO_SESSIONS",
          f"comp={bal5b['COMPENSATION']}, elig={elig5b['code']}")

    # TEST 8: compensation expiry blocks attendance unless admin override
    p8 = make_test_player(conn, "QA-T8")
    ent.grant_entitlement(conn, p8, "COMPENSATION", 2, expires_at=(date.today() - timedelta(days=1)).isoformat(),
                           reason_code="OTHER", reason_text="QA expired grant", user_id=ADMIN_USER_ID)
    elig8 = subs.get_attendance_eligibility(conn, p8)
    blocked = elig8["code"] == "NO_SESSIONS"
    ex(conn, "UPDATE players SET attendance_override=1, attendance_override_note='QA override' WHERE id=?", (p8,))
    elig8b = subs.get_attendance_eligibility(conn, p8)
    check("TEST 8: expired compensation blocks attendance; admin override lifts it",
          blocked and elig8b["code"] == "ADMIN_OVERRIDE", f"blocked={blocked}, override={elig8b['code']}")
    ex(conn, "UPDATE players SET attendance_override=0 WHERE id=?", (p8,))

    # TEST 9: renew while holding 2 compensation sessions -> 12 regular + 2 compensation, nothing lost
    p9 = make_test_player(conn, "QA-T9")
    ent.grant_entitlement(conn, p9, "COMPENSATION", 2, expires_at=(date.today() + timedelta(days=30)).isoformat(),
                           reason_code="OTHER", reason_text="QA pre-renewal comp", user_id=ADMIN_USER_ID)
    subs.create_subscription(conn, p9, pkg, date.today().isoformat(), 699, 0, 699, "CASH", "QA9",
                              user_id=ADMIN_USER_ID, sessions_count_override=12)
    bal9 = ent.get_balances(conn, p9)
    check("TEST 9: renewal preserves compensation + adds regular (12+2)",
          bal9["REGULAR"] == 12 and bal9["COMPENSATION"] == 2 and bal9["TOTAL"] == 14,
          f"REG={bal9['REGULAR']} COMP={bal9['COMPENSATION']} TOTAL={bal9['TOTAL']}")

    # TEST 10: FEFO - nearest-expiring compensation consumed before another compensation batch / regular
    p10 = make_test_player(conn, "QA-T10")
    near = ent.grant_entitlement(conn, p10, "COMPENSATION", 1, expires_at=(date.today() + timedelta(days=5)).isoformat(),
                                  reason_code="OTHER", reason_text="near", user_id=ADMIN_USER_ID)
    far = ent.grant_entitlement(conn, p10, "COMPENSATION", 1, expires_at=(date.today() + timedelta(days=25)).isoformat(),
                                 reason_code="OTHER", reason_text="far", user_id=ADMIN_USER_ID)
    subs.create_subscription(conn, p10, pkg, date.today().isoformat(), 699, 0, 699, "CASH", "QA10",
                              user_id=ADMIN_USER_ID, sessions_count_override=12)
    s10 = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p10,))["group_id"])
    ent_id, etype, _ = ent.consume_one_session(conn, p10, s10, ADMIN_USER_ID)
    check("TEST 10: FEFO consumes nearest-expiring compensation batch first",
          ent_id == near, f"consumed batch {ent_id}, expected nearest batch {near} (far batch was {far})")

    # TEST 11: coach without permission cannot add compensation sessions (checked at the RBAC layer)
    from business.rbac import has_permission
    coach_user = q1(conn, "SELECT * FROM users WHERE role='COACH' LIMIT 1")
    allowed = has_permission(conn, coach_user, "manage_compensation")
    check("TEST 11: coach role is denied 'manage_compensation' permission", allowed is False, f"allowed={allowed}")

    # TEST 12: legacy player finishing last session enters conversion pipeline, NOT auto-FOUQ
    p12 = make_test_player(conn, "QA-T12", player_type="LEGACY")
    ent.grant_entitlement(conn, p12, "LEGACY", 1, reason_text="QA legacy grant", user_id=ADMIN_USER_ID)
    s12 = make_test_session(conn, q1(conn, "SELECT group_id FROM players WHERE id=?", (p12,))["group_id"])
    att.mark_attendance(conn, s12, p12, "PRESENT", ADMIN_USER_ID)
    leg.check_conversion_eligibility(conn, p12, ADMIN_USER_ID)
    p12_row = q1(conn, "SELECT * FROM players WHERE id=?", (p12,))
    check("TEST 12: legacy player completing sessions -> CONVERSION_PENDING (not auto-FOUQ)",
          p12_row["status"] == "CONVERSION_PENDING" and p12_row["player_type"] == "LEGACY",
          f"status={p12_row['status']}, type={p12_row['player_type']}")

    # TEST 13: parent with two children sees exactly those two
    from business.rbac import parent_player_ids
    parent_row = q1(conn, """SELECT pr.* FROM parents pr JOIN parent_players pp1 ON pp1.parent_id=pr.id
                             GROUP BY pr.id HAVING COUNT(*) >= 2 LIMIT 1""")
    seen = parent_player_ids(conn, parent_row["user_id"]) if parent_row else []
    check("TEST 13: parent with two sons sees exactly two linked players",
          parent_row is not None and len(seen) == 2, f"parent={parent_row}, seen={seen}")

    # TEST 14: coach marking a group updates each player's own file independently
    grp_row = q1(conn, "SELECT id FROM groups_ LIMIT 1")
    roster_ids = []
    for tag in ("QA-T14A", "QA-T14B"):
        pid14 = make_test_player(conn, tag, group_id=grp_row["id"])
        subs.create_subscription(conn, pid14, pkg, date.today().isoformat(), 699, 0, 699, "CASH", tag,
                                  user_id=ADMIN_USER_ID, sessions_count_override=12)
        roster_ids.append(pid14)
    s14 = make_test_session(conn, grp_row["id"], days_ago=9)
    bulk_results = att.mark_all_present(conn, s14, roster_ids, ADMIN_USER_ID)
    ok14 = all(r["ok"] for r in bulk_results) and all(
        q1(conn, "SELECT status FROM attendance WHERE training_session_id=? AND player_id=?", (s14, pid))["status"] == "PRESENT"
        for pid in roster_ids
    )
    check("TEST 14: coach bulk-marks a group -> each player's own record updates", ok14, str(bulk_results))

    # TEST 15: redeem a reward -> points and stock both decrease correctly
    from business.rewards import request_redemption, update_redemption_status
    from business.points import award_points, get_balance
    p15 = make_test_player(conn, "QA-T15")
    award_points(conn, p15, 100, "QA seed points", "ADMIN", ADMIN_USER_ID)
    reward = q1(conn, "SELECT * FROM rewards WHERE cost <= 100 ORDER BY cost DESC LIMIT 1")
    stock_before = reward["stock"]
    redemption_id = request_redemption(conn, p15, reward["id"], ADMIN_USER_ID)
    bal15 = get_balance(conn, p15)
    stock_after = q1(conn, "SELECT stock FROM rewards WHERE id=?", (reward["id"],))["stock"]
    check("TEST 15: redeeming a reward decrements both points and stock",
          bal15 == 100 - reward["cost"] and stock_after == stock_before - 1,
          f"balance={bal15}, stock {stock_before}->{stock_after}")

    # TEST 16: Player Report PDF generation with correct Arabic text (smoke-checked via HTTP in manual QA;
    # here we validate the underlying data path that feeds the template renders without error)
    from business.assessments import player_development_timeline
    try:
        player_development_timeline(conn, p)
        pdf_data_ok = True
    except Exception as e:
        pdf_data_ok = False
    check("TEST 16: player report data path (feeds Arabic RTL PDF template) builds without error", pdf_data_ok)

    # TEST 17: Compensation report numbers match the ledger exactly
    from business.reports import outstanding_sessions_report
    rows = outstanding_sessions_report(conn)
    row9 = next((r for r in rows if r["id"] == p9), None)
    ledger_comp_remaining = q1(
        conn, "SELECT COALESCE(SUM(quantity_remaining),0) c FROM session_entitlements WHERE player_id=? AND type='COMPENSATION' AND status='ACTIVE'",
        (p9,))["c"]
    check("TEST 17: outstanding-sessions report matches the ledger exactly",
          row9 is not None and row9["comp_remaining"] == ledger_comp_remaining,
          f"report={row9['comp_remaining'] if row9 else None}, ledger={ledger_comp_remaining}")

    # TEST 18: renewing preserves subscription history (old rows untouched)
    subs_before = q(conn, "SELECT id FROM subscriptions WHERE player_id=?", (p9,))
    subs.renew_subscription(conn, p9, pkg, date.today().isoformat(), 699, 0, 699, "CASH", "QA18", user_id=ADMIN_USER_ID)
    subs_after = q(conn, "SELECT id FROM subscriptions WHERE player_id=?", (p9,))
    check("TEST 18: renewal creates a new row and keeps subscription history",
          len(subs_after) == len(subs_before) + 1, f"before={len(subs_before)}, after={len(subs_after)}")

    conn.commit()
    conn.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} tests passed.")
    os.remove(TMP_DB)
    return RESULTS


if __name__ == "__main__":
    run()
