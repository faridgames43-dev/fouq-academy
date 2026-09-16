"""Seed data for FOUQ Academy - realistic, varied player states so the
system can be tested immediately (see spec section 68 / 73 TEST scenarios)."""
import json
from datetime import date, timedelta
from werkzeug.security import generate_password_hash

from db import init_db, get_conn, ex, q1
from business.settings_lib import ensure_defaults, set_setting
from business import entitlements as ent
from business import subscriptions as subs
from business import attendance as att
from business import assessments as asm
from business import points as pts
from business import achievements as ach
from business import levels as lvl
from business import legacy as leg
from business import crm

PASSWORD = "Fouq@2026"


def d(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def backdate(conn, table, row_id, days_ago):
    ts = (date.today() - timedelta(days=days_ago)).isoformat() + " 09:00:00"
    ex(conn, f"UPDATE {table} SET created_at=? WHERE id=?", (ts, row_id))


def main():
    init_db()
    conn = get_conn()
    ensure_defaults(conn)
    conn.commit()

    # ---------------- Branches & facilities ----------------
    dammam = ex(conn, "INSERT INTO branches(name, city, address) VALUES (?,?,?)",
                ("فرع الدمام", "الدمام", "حي الجامعيين - صالة جمعية تواصل للتنمية الأهلية"))
    khobar = ex(conn, "INSERT INTO branches(name, city, address, active) VALUES (?,?,?,0)",
                ("فرع الخبر (قريبًا)", "الخبر", "قيد التخطيط"))
    facility = ex(conn, "INSERT INTO facilities(branch_id, name) VALUES (?,?)", (dammam, "الصالة الرئيسية"))

    # ---------------- Categories ----------------
    baraem = ex(conn, "INSERT INTO categories(name, min_age, max_age) VALUES (?,?,?)", ("البراعم", 7, 9))
    ashbal = ex(conn, "INSERT INTO categories(name, min_age, max_age) VALUES (?,?,?)", ("الأشبال", 10, 12))

    # ---------------- Settings tune ----------------
    set_setting(conn, "compensation_expiry_days", "30")

    # ---------------- Users & roles ----------------
    phone_seq = [50 * 1000000 + i for i in range(1, 200)]
    pi = iter(phone_seq)

    def phone():
        return "05" + str(next(pi)).zfill(8)[-8:]

    super_admin = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
                      ("مؤسسة فريد للترفيه - الإدارة العليا", "superadmin@fouq.sa", phone(),
                       generate_password_hash(PASSWORD), "SUPER_ADMIN"))
    pm = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
            ("مدير المشروع", "pm@fouq.sa", phone(), generate_password_hash(PASSWORD), "PROJECT_MANAGER", dammam))
    branch_mgr = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
                     ("مدير فرع الدمام", "branchmgr@fouq.sa", phone(), generate_password_hash(PASSWORD), "BRANCH_MANAGER", dammam))
    supervisor = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
                     ("المشرف التشغيلي", "supervisor@fouq.sa", phone(), generate_password_hash(PASSWORD), "SUPERVISOR", dammam))

    coach1_user = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
                      ("الكابتن سعد - البراعم", "coach1@fouq.sa", phone(), generate_password_hash(PASSWORD), "COACH", dammam))
    coach2_user = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role,branch_id) VALUES (?,?,?,?,?,?)",
                      ("الكابتن فهد - الأشبال", "coach2@fouq.sa", phone(), generate_password_hash(PASSWORD), "COACH", dammam))
    coach1 = ex(conn, "INSERT INTO coaches(user_id, name, phone, branch_id) VALUES (?,?,?,?)",
                (coach1_user, "الكابتن سعد", phone(), dammam))
    coach2 = ex(conn, "INSERT INTO coaches(user_id, name, phone, branch_id) VALUES (?,?,?,?)",
                (coach2_user, "الكابتن فهد", phone(), dammam))

    # ---------------- Groups ----------------
    group_baraem = ex(conn, "INSERT INTO groups_(name, branch_id, category_id, coach_id) VALUES (?,?,?,?)",
                       ("مجموعة البراعم أ", dammam, baraem, coach1))
    group_ashbal = ex(conn, "INSERT INTO groups_(name, branch_id, category_id, coach_id) VALUES (?,?,?,?)",
                       ("مجموعة الأشبال أ", dammam, ashbal, coach2))

    # ---------------- Packages ----------------
    pkg_baraem = ex(conn, """INSERT INTO packages(name, branch_id, category_id, price, duration_days, sessions_count,
                             days_per_week, freeze_policy_days, compensation_expiry_days)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                     ("باقة البراعم الشهرية", dammam, baraem, 699, 30, 12, 3, 7, 30))
    pkg_ashbal = ex(conn, """INSERT INTO packages(name, branch_id, category_id, price, duration_days, sessions_count,
                             days_per_week, freeze_policy_days, compensation_expiry_days)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                     ("باقة الأشبال الشهرية", dammam, ashbal, 699, 30, 12, 3, 7, 30))
    conn.commit()
    pkg_baraem_row = q1(conn, "SELECT * FROM packages WHERE id=?", (pkg_baraem,))
    pkg_ashbal_row = q1(conn, "SELECT * FROM packages WHERE id=?", (pkg_ashbal,))

    # ---------------- Levels ----------------
    level_defs = [
        (1, "الانطلاقة", 0, 0, 0, "بداية الرحلة مع فوق"),
        (2, "الصاعد", 60, 50, 55, "استمرارية والتزام مبدئي"),
        (3, "المنافس", 70, 62, 65, "جاهزية للمنافسة"),
        (4, "المتقدم", 80, 74, 75, "مستوى فني متقدم"),
        (5, "النخبة", 90, 85, 85, "نخبة أكاديمية فوق"),
    ]
    level_ids = {}
    for order_, name, a, o, disc, desc in level_defs:
        lid = ex(conn, """INSERT INTO levels(level_order, name, min_attendance_rate, min_overall_score,
                          min_discipline_score, description) VALUES (?,?,?,?,?,?)""",
                 (order_, name, a, o, disc, desc))
        level_ids[order_] = lid

    # ---------------- Assessment categories & metrics ----------------
    cat_ids = {}
    for code, name, weight in [("SKILL", "المهاري", 40), ("FITNESS", "اللياقي", 25),
                                ("BEHAVIOR", "السلوكي", 20), ("DISCIPLINE", "الانضباط", 15)]:
        cat_ids[code] = ex(conn, "INSERT INTO assessment_categories(code,name,weight) VALUES (?,?,?)", (code, name, weight))

    metric_ids = {"SKILL": [], "FITNESS": [], "BEHAVIOR": [], "DISCIPLINE": []}
    metrics_map = {
        "SKILL": ["التحكم", "التمرير", "التسديد", "المراوغة", "الاستلام", "اتخاذ القرار"],
        "FITNESS": ["السرعة", "الرشاقة", "التحمل", "التوازن", "التوافق الحركي"],
        "BEHAVIOR": ["الاحترام", "التعاون", "الروح الرياضية", "ضبط النفس", "التعامل مع الفوز والخسارة"],
        "DISCIPLINE": ["الحضور", "الالتزام بالوقت", "اللباس", "اتباع التعليمات", "الجاهزية"],
    }
    for code, names in metrics_map.items():
        for n in names:
            mid = ex(conn, "INSERT INTO assessment_metrics(category_id, name) VALUES (?,?)", (cat_ids[code], n))
            metric_ids[code].append(mid)

    # ---------------- Achievements ----------------
    achievement_defs = [
        ("FIRST_SESSION", "أول خطوة", "أول حصة", "👟", 10),
        ("STREAK_5", "ما يفوّت", "5 حصص متتالية بدون غياب", "🔥", 15),
        ("COMMITTED_10", "ملتزم فوق", "10 حصص بانضباط", "🛡️", 20),
        ("TEAM_SPIRIT", "روح الفريق", "إنجاز سلوكي مميز", "🤝", 15),
        ("DEVELOPMENT_LEAP", "قفزة", "تطور ملحوظ في التقييم", "🚀", 25),
        ("FIRST_PROMOTION", "الصاعد", "أول ترقية مستوى", "⬆️", 30),
        ("FIRST_100", "100 فوق", "أول 100 من رصيد فوق", "💯", 10),
        ("LOYAL_3X", "وفيّ لفوق", "3 تجديدات اشتراك", "🏆", 40),
    ]
    for code, name, desc, icon, pr in achievement_defs:
        ex(conn, "INSERT INTO achievements(code,name,description,icon,points_reward) VALUES (?,?,?,?,?)",
           (code, name, desc, icon, pr))

    # ---------------- Rewards ----------------
    reward_defs = [
        ("بروش فوق", "دبوس معدني بشعار الأكاديمية", 30, 50),
        ("قارورة فوق", "قارورة مياه رياضية بشعار فوق", 60, 30),
        ("كرة فوق", "كرة قدم تدريبية مطبوعة", 150, 15),
        ("تيشيرت فوق", "تيشيرت رياضي رسمي", 200, 25),
        ("حقيبة فوق", "حقيبة ظهر رياضية", 350, 10),
        ("تجربة خاصة", "حصة تدريب خاصة مع الكابتن", 400, 5),
        ("هدية سرية", "مفاجأة من إدارة الأكاديمية", 500, 5),
    ]
    for name, desc, cost, stock in reward_defs:
        ex(conn, "INSERT INTO rewards(name,description,cost,stock) VALUES (?,?,?,?)", (name, desc, cost, stock))

    conn.commit()

    # ---------------- Session pools (past training sessions) ----------------
    def build_pool(group_id, coach_id):
        ids = []
        for days_ago in range(2, 44, 3):
            sid = ex(conn, """INSERT INTO training_sessions(branch_id, group_id, coach_id, facility_id,
                              session_date, start_time, end_time, goal, status)
                              VALUES (?,?,?,?,?,?,?,?,'COMPLETED')""",
                     (dammam, group_id, coach_id, facility, d(days_ago), "17:00", "18:30", "تمرين أساسي"))
            ids.append(sid)
        return ids  # index 0 = most recent (days_ago=2)

    pool_baraem = build_pool(group_baraem, coach1)
    pool_ashbal = build_pool(group_ashbal, coach2)
    conn.commit()

    def mark(pool, idx, player_id, status):
        att.mark_attendance(conn, pool[idx], player_id, status, user_id=pm)

    # ---------------- Parents ----------------
    def make_parent(name, has_login=True):
        uid = None
        if has_login:
            uid = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
                      (name, None, phone(), generate_password_hash(PASSWORD), "PARENT"))
        pid = ex(conn, "INSERT INTO parents(user_id, name, phone) VALUES (?,?,?)", (uid, name, phone()))
        return pid

    parent_ahmadi = make_parent("محمد الأحمدي")     # will have TWO sons (players 1 & 6) -> TEST13
    parent_qahtani = make_parent("عبدالرحمن القحطاني")
    parent_dosari = make_parent("سعد الدوسري")
    parent_otaibi = make_parent("فهد العتيبي")
    parent_ghamdi = make_parent("علي الغامدي")
    parent_mutairi = make_parent("ماجد المطيري")
    parent_zahrani = make_parent("خالد الزهراني")
    parent_harbi = make_parent("تركي الحربي")
    parent_subaie = make_parent("عبدالله السبيعي")
    parent_anzi = make_parent("سلمان العنزي")
    parent_rashidi = make_parent("بدر الرشيدي")
    parent_buqami = make_parent("ناصر البقمي")
    parent_shamri = make_parent("فيصل الشمري")
    parent_alsaeed = make_parent("عبدالمجيد آل سعيد")

    def next_code(prefix, n):
        return f"{prefix}-{n:04d}"

    def make_player(code_n, first, last, category_id, branch_id, group_id, coach_id, parent_id,
                     player_type="FOUQ", dob_years_ago=9):
        code = next_code("FOUQ", code_n)
        uid = ex(conn, "INSERT INTO users(name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
                 (f"{first} {last}", None, phone(), generate_password_hash(PASSWORD), "PLAYER"))
        pid = ex(conn, """INSERT INTO players(player_code, user_id, first_name, last_name, dob, gender,
                          category_id, branch_id, group_id, coach_id, player_type, referral_code)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (code, uid, first, last, d(dob_years_ago * 365), "M", category_id, branch_id, group_id,
                  coach_id, player_type, f"REF-{code_n:04d}"))
        ex(conn, "INSERT INTO parent_players(parent_id, player_id) VALUES (?,?)", (parent_id, pid))
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (pid,))
        return pid

    # ===================================================================
    # 15 PLAYERS - deliberately varied operational states
    # ===================================================================

    # 1) عبدالله الأحمدي - active, mid-cycle usage
    p1 = make_player(1, "عبدالله", "الأحمدي", ashbal, dammam, group_ashbal, coach2, parent_ahmadi)
    sub1 = subs.create_subscription(conn, p1, pkg_ashbal_row, d(20), 699, 0, 699, "CASH", "INV-0001", user_id=pm)
    backdate(conn, "subscriptions", sub1, 20)
    for i in range(6):
        mark(pool_ashbal, i, p1, "PRESENT")
    asm.create_assessment(conn, p1, "INITIAL", coach2, {
        metric_ids["SKILL"][0]: 55, metric_ids["SKILL"][1]: 58, metric_ids["FITNESS"][0]: 60,
        metric_ids["BEHAVIOR"][0]: 70, metric_ids["DISCIPLINE"][0]: 75}, "تقييم أولي", user_id=coach2_user)

    # 2) سلطان القحطاني - active, low renewal risk, great attendance
    p2 = make_player(2, "سلطان", "القحطاني", baraem, dammam, group_baraem, coach1, parent_qahtani)
    sub2 = subs.create_subscription(conn, p2, pkg_baraem_row, d(5), 699, 0, 699, "MADA", "INV-0002", user_id=pm)
    mark(pool_baraem, 0, p2, "PRESENT")
    mark(pool_baraem, 1, p2, "PRESENT")
    asm.create_assessment(conn, p2, "INITIAL", coach1, {
        metric_ids["SKILL"][0]: 68, metric_ids["FITNESS"][0]: 72, metric_ids["BEHAVIOR"][0]: 85,
        metric_ids["DISCIPLINE"][0]: 88}, "أداء ممتاز", user_id=coach1_user)

    # 3) فيصل الدوسري - expiring soon (5 days)
    p3 = make_player(3, "فيصل", "الدوسري", ashbal, dammam, group_ashbal, coach2, parent_dosari)
    sub3 = subs.create_subscription(conn, p3, pkg_ashbal_row, d(25), 699, 0, 699, "CASH", "INV-0003", user_id=pm)
    backdate(conn, "subscriptions", sub3, 25)
    for i in range(8):
        mark(pool_ashbal, i, p3, "PRESENT")

    # 4) تركي العتيبي - expired, no remaining sessions -> blocked, needs renewal
    p4 = make_player(4, "تركي", "العتيبي", baraem, dammam, group_baraem, coach1, parent_otaibi)
    sub4 = subs.create_subscription(conn, p4, pkg_baraem_row, d(40), 699, 0, 699, "CASH", "INV-0004", user_id=pm)
    backdate(conn, "subscriptions", sub4, 40)
    for i in range(12):
        mark(pool_baraem, i, p4, "PRESENT")

    # 5) ياسر الغامدي - expired subscription BUT has 3 compensation sessions valid
    p5 = make_player(5, "ياسر", "الغامدي", ashbal, dammam, group_ashbal, coach2, parent_ghamdi)
    sub5 = subs.create_subscription(conn, p5, pkg_ashbal_row, d(40), 699, 0, 699, "CASH", "INV-0005", user_id=pm)
    backdate(conn, "subscriptions", sub5, 40)
    for i in range(12):
        mark(pool_ashbal, i, p5, "PRESENT")
    ent.grant_entitlement(conn, p5, "COMPENSATION", 3, expires_at=d(-20),
                           reason_code="CANCELLED_SESSION",
                           reason_text="تعويض عن إلغاء 3 حصص من الأكاديمية بسبب عطل بالصالة", user_id=pm)

    # 6) عبدالعزيز الأحمدي - one session remaining (same parent as p1 -> TEST13)
    p6 = make_player(6, "عبدالعزيز", "الأحمدي", baraem, dammam, group_baraem, coach1, parent_ahmadi)
    sub6 = subs.create_subscription(conn, p6, pkg_baraem_row, d(22), 699, 0, 699, "CASH", "INV-0006", user_id=pm)
    backdate(conn, "subscriptions", sub6, 22)
    for i in range(11):
        mark(pool_baraem, i, p6, "PRESENT")

    # 7) خالد المطيري - LEGACY player with sessions remaining
    p7 = make_player(7, "خالد", "المطيري", ashbal, dammam, group_ashbal, coach2, parent_mutairi,
                      player_type="LEGACY")
    ent.grant_entitlement(conn, p7, "LEGACY", 5, reason_text="حصص متبقية من نادي تواصل الرياضي", user_id=pm)
    for i in range(3):
        mark(pool_ashbal, i, p7, "PRESENT")

    # 8) سعود الزهراني - LEGACY player, completed sessions -> conversion pipeline
    p8 = make_player(8, "سعود", "الزهراني", baraem, dammam, group_baraem, coach1, parent_zahrani,
                      player_type="LEGACY")
    ent.grant_entitlement(conn, p8, "LEGACY", 4, reason_text="حصص متبقية من نادي تواصل الرياضي", user_id=pm)
    for i in range(4):
        mark(pool_baraem, i, p8, "PRESENT")
    leg.check_conversion_eligibility(conn, p8, user_id=pm)

    # 9) ناصر الحربي - renewed 3 times -> loyalty achievement
    p9 = make_player(9, "ناصر", "الحربي", ashbal, dammam, group_ashbal, coach2, parent_harbi)
    old1 = subs.create_subscription(conn, p9, pkg_ashbal_row, d(95), 699, 0, 699, "CASH", "INV-0007a", user_id=pm)
    backdate(conn, "subscriptions", old1, 95)
    ent.administrative_adjustment(conn, p9, "REGULAR", -12, "استهلاك كامل الدورة الأولى (بيانات تجريبية)", pm)
    old2 = subs.create_subscription(conn, p9, pkg_ashbal_row, d(65), 699, 0, 699, "CASH", "INV-0007b", user_id=pm)
    backdate(conn, "subscriptions", old2, 65)
    ent.administrative_adjustment(conn, p9, "REGULAR", -12, "استهلاك كامل الدورة الثانية (بيانات تجريبية)", pm)
    cur9 = subs.create_subscription(conn, p9, pkg_ashbal_row, d(10), 699, 0, 699, "CASH", "INV-0007c", user_id=pm)
    for i in range(4):
        mark(pool_ashbal, i, p9, "PRESENT")
    ach.check_after_renewal(conn, p9, user_id=pm)

    # 10) بندر السبيعي - HIGH renewal risk
    p10 = make_player(10, "بندر", "السبيعي", baraem, dammam, group_baraem, coach1, parent_subaie)
    sub10 = subs.create_subscription(conn, p10, pkg_baraem_row, d(27), 699, 0, 699, "CASH", "INV-0008", user_id=pm)
    backdate(conn, "subscriptions", sub10, 27)
    pattern10 = ["ABSENT", "ABSENT", "PRESENT", "ABSENT", "PRESENT", "ABSENT", "ABSENT", "PRESENT", "ABSENT"]
    for i, st in enumerate(pattern10):
        mark(pool_baraem, i, p10, st)

    # 11) راكان العنزي - LOW renewal risk
    p11 = make_player(11, "راكان", "العنزي", ashbal, dammam, group_ashbal, coach2, parent_anzi)
    sub11 = subs.create_subscription(conn, p11, pkg_ashbal_row, d(2), 699, 0, 699, "MADA", "INV-0009", user_id=pm)
    mark(pool_ashbal, 0, p11, "PRESENT")
    asm.create_assessment(conn, p11, "INITIAL", coach2, {
        metric_ids["SKILL"][0]: 75, metric_ids["FITNESS"][0]: 80, metric_ids["BEHAVIOR"][0]: 90,
        metric_ids["DISCIPLINE"][0]: 92}, "لاعب متميز الالتزام", user_id=coach2_user)

    # 12) حمد الرشيدي - active + pending referral
    p12 = make_player(12, "حمد", "الرشيدي", baraem, dammam, group_baraem, coach1, parent_rashidi)
    sub12 = subs.create_subscription(conn, p12, pkg_baraem_row, d(8), 699, 0, 699, "CASH", "INV-0010", user_id=pm)
    for i in range(2):
        mark(pool_baraem, i, p12, "PRESENT")
    crm.create_referral(conn, p12, "سامي الرشيدي", "05" + "5" * 8, user_id=pm)

    # 13) مشعل البقمي - active + pending reward redemption
    p13 = make_player(13, "مشعل", "البقمي", ashbal, dammam, group_ashbal, coach2, parent_buqami)
    sub13 = subs.create_subscription(conn, p13, pkg_ashbal_row, d(6), 699, 0, 699, "CASH", "INV-0011", user_id=pm)
    for i in range(3):
        mark(pool_ashbal, i, p13, "PRESENT")
    pts.award_points(conn, p13, 220, "التزام ممتاز خلال الشهر", "ATTENDANCE", pm)
    from business.rewards import request_redemption
    request_redemption(conn, p13, q1(conn, "SELECT id FROM rewards WHERE name='قارورة فوق'")["id"], pm)

    # 14) عمر الشمري - promoted to level 2
    p14 = make_player(14, "عمر", "الشمري", baraem, dammam, group_baraem, coach1, parent_shamri)
    sub14 = subs.create_subscription(conn, p14, pkg_baraem_row, d(20), 699, 0, 699, "CASH", "INV-0012", user_id=pm)
    for i in range(9):
        mark(pool_baraem, i, p14, "PRESENT")
    a14_initial = asm.create_assessment(conn, p14, "INITIAL", coach1, {
        metric_ids["SKILL"][0]: 50, metric_ids["FITNESS"][0]: 52, metric_ids["BEHAVIOR"][0]: 60,
        metric_ids["DISCIPLINE"][0]: 58}, "خط الأساس", user_id=coach1_user)
    a14_periodic = asm.create_assessment(conn, p14, "PERIODIC", coach1, {
        metric_ids["SKILL"][0]: 68, metric_ids["FITNESS"][0]: 70, metric_ids["BEHAVIOR"][0]: 78,
        metric_ids["DISCIPLINE"][0]: 80}, "تحسن ملحوظ بعد 60 يومًا", user_id=coach1_user)
    lvl.current_level(conn, p14)  # ensures LEVEL 1 baseline exists
    lvl.approve_promotion(conn, p14, supervisor, "SUPERVISOR")
    ach.check_after_promotion(conn, p14, user_id=supervisor)
    ach.check_development_leap(conn, p14, 55, 74, user_id=supervisor)

    # 15) فهد آل سعيد - CRM trial -> converted, onboarding incomplete
    lead15 = crm.create_lead(conn, "عبدالمجيد آل سعيد", "فهد", 8, phone(), "إنستغرام", "حملة رمضان", pm, dammam)
    trial15 = crm.book_trial(conn, lead15, d(4), group_baraem, pm)
    crm.record_trial_result(conn, trial15, True, "لاعب نشيط ومتحمس", "يُنصح بالتسجيل في مجموعة البراعم", pm)
    p15 = make_player(15, "فهد", "آل سعيد", baraem, dammam, group_baraem, coach1, parent_alsaeed)
    crm.convert_lead_to_player(conn, trial15, p15, pm)
    sub15 = subs.create_subscription(conn, p15, pkg_baraem_row, d(1), 699, 0, 350, "CASH", "INV-0013", user_id=pm)
    ex(conn, "UPDATE players SET onboarding_json=? WHERE id=?", (json.dumps({
        "account_created": True, "parent_linked": True, "group_assigned": True, "subscription_created": True,
        "player_id_issued": True, "qr_issued": False, "initial_assessment": False, "kit_delivered": False,
        "platform_explained": False, "welcome_sent": False,
    }), p15))

    for pid in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12, p13, p14, p15]:
        ach.check_after_attendance(conn, pid, user_id=pm)
        ach.check_after_points(conn, pid, user_id=pm)

    conn.commit()
    conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    main()
