"""Bulk player import from an Excel (.xlsx) template.

Flow: an admin downloads the template (build_template_workbook, pre-filled
with the academy's CURRENT categories/branches/groups so names always match),
fills one row per player, uploads it back. parse_workbook() validates every
row against live data and returns either a clean list of rows ready to
create, or a list of row-by-row errors — nothing is created unless the whole
file is valid (all-or-nothing, same spirit as reset_demo.py's safety net).

Every imported player gets the SAME "unified" password (chosen by the admin
in the upload form) plus a linked parent account (deduped by phone, same as
the single-player /players/new flow). Both accounts are forced to change
their password on first login (must_reset_password=1 — see
business/accounts.py), so the shared password can't be reused after
everyone's first sign-in.
"""
from datetime import date, datetime
from db import q, q1, ex
from business.accounts import create_user_account, create_user_account_with_password, find_parent_by_phone
from business.audit import log as audit_log

HEADERS = [
    "الاسم الأول", "اسم العائلة", "تاريخ الميلاد (مثال: 2016-05-20)", "الجنس (ذكر / أنثى)",
    "الفئة", "الفرع", "المجموعة (اختياري)", "اسم ولي الأمر (اختياري)", "جوال ولي الأمر (اختياري)",
]
COL_FIRST, COL_LAST, COL_DOB, COL_GENDER, COL_CATEGORY, COL_BRANCH, COL_GROUP, COL_PARENT_NAME, COL_PARENT_PHONE = range(9)

GENDER_MAP = {"ذكر": "M", "أنثى": "F", "انثى": "F", "m": "M", "f": "F", "M": "M", "F": "F"}


def _norm(v):
    if v is None:
        return ""
    return str(v).strip()


def build_template_workbook(conn):
    """Returns an openpyxl Workbook: a fillable sheet + a live reference
    sheet listing the academy's current categories/branches/groups, so the
    admin always copies exact, currently-valid names."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    categories = q(conn, "SELECT name FROM categories WHERE active=1 ORDER BY name")
    branches = q(conn, "SELECT name FROM branches WHERE active=1 ORDER BY name")
    groups = q(conn, """SELECT g.name, b.name as branch_name, c.name as category_name
                        FROM groups_ g JOIN branches b ON b.id=g.branch_id
                        JOIN categories c ON c.id=g.category_id WHERE g.active=1 ORDER BY g.name""")

    wb = Workbook()
    ws = wb.active
    ws.title = "بيانات اللاعبين"
    ws.sheet_view.rightToLeft = True

    header_fill = PatternFill("solid", fgColor="0B1F35")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    for i, h in enumerate(HEADERS, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = 22
    ws.row_dimensions[1].height = 34

    example = ["مثال: عبدالله", "الأحمدي", "2016-05-20", "ذكر",
               categories[0]["name"] if categories else "الأشبال",
               branches[0]["name"] if branches else "فرع الدمام", "", "سلمان الأحمدي", "0512345678"]
    example_font = Font(italic=True, color="9AA3B5")
    for i, v in enumerate(example, start=1):
        c = ws.cell(row=2, column=i, value=v)
        c.font = example_font
    ws.row_dimensions[2].height = 18

    for r in range(3, 60):
        for i in range(1, len(HEADERS) + 1):
            ws.cell(row=r, column=i)

    # ---- reference sheet (read-only info for the admin) ----
    ref = wb.create_sheet("الفئات والفروع والمجموعات")
    ref.sheet_view.rightToLeft = True
    ref["A1"] = "الفئات المتاحة"
    ref["A1"].font = Font(bold=True, size=12)
    ref["B1"] = "الفروع المتاحة"
    ref["B1"].font = Font(bold=True, size=12)
    ref["C1"] = "اسم المجموعة (اكتب هذا فقط في عمود المجموعة)"
    ref["C1"].font = Font(bold=True, size=12)
    ref["D1"] = "فرعها"
    ref["D1"].font = Font(bold=True, size=12)
    ref["E1"] = "فئتها"
    ref["E1"].font = Font(bold=True, size=12)
    for i, cat in enumerate(categories, start=2):
        ref.cell(row=i, column=1, value=cat["name"])
    for i, br in enumerate(branches, start=2):
        ref.cell(row=i, column=2, value=br["name"])
    for i, gr in enumerate(groups, start=2):
        ref.cell(row=i, column=3, value=gr["name"])
        ref.cell(row=i, column=4, value=gr["branch_name"])
        ref.cell(row=i, column=5, value=gr["category_name"])
    for col, w in (("A", 22), ("B", 22), ("C", 26), ("D", 22), ("E", 22)):
        ref.column_dimensions[col].width = w

    # dropdown validation on the fillable sheet, sourced from the reference sheet
    if categories:
        dv_cat = DataValidation(type="list", formula1=f"='الفئات والفروع والمجموعات'!$A$2:$A${1+len(categories)}", allow_blank=True)
        ws.add_data_validation(dv_cat)
        dv_cat.add(f"E3:E200")
    if branches:
        dv_branch = DataValidation(type="list", formula1=f"='الفئات والفروع والمجموعات'!$B$2:$B${1+len(branches)}", allow_blank=True)
        ws.add_data_validation(dv_branch)
        dv_branch.add(f"F3:F200")
    if groups:
        dv_group = DataValidation(type="list", formula1=f"='الفئات والفروع والمجموعات'!$C$2:$C${1+len(groups)}", allow_blank=True)
        ws.add_data_validation(dv_group)
        dv_group.add(f"G3:G200")
    dv_gender = DataValidation(type="list", formula1='"ذكر,أنثى"', allow_blank=True)
    ws.add_data_validation(dv_gender)
    dv_gender.add("D3:D200")

    instructions = wb.create_sheet("تعليمات", 0)
    instructions.sheet_view.rightToLeft = True
    instructions["A1"] = "تعليمات تعبئة النموذج"
    instructions["A1"].font = Font(bold=True, size=14)
    lines = [
        "1) اذهب لتبويب «بيانات اللاعبين» وابدأ التعبئة من الصف الثالث (احذف صف المثال الرمادي في الصف الثاني، أو اتركه — سيتم تجاهله تلقائيًا).",
        "2) الحقول المطلوبة: الاسم الأول، اسم العائلة، تاريخ الميلاد، الجنس، الفئة، الفرع.",
        "3) اكتب الفئة والفرع بنفس الإملاء الموجود بالضبط في تبويب «الفئات والفروع والمجموعات» — أو اختر من القائمة المنسدلة في كل خلية. عمود المجموعة اختياري: اكتب اسم المجموعة فقط (عمود C في تبويب المرجع) بدون الفرع أو الفئة.",
        "4) تاريخ الميلاد بصيغة سنة-شهر-يوم، مثال: 2016-05-20.",
        "5) اسم ولي الأمر وجواله اختياريان لكن يُفضّل تعبئتهما — إذا كان لولي الأمر أكثر من لاعب، استخدم نفس رقم الجوال بالضبط لكل أبنائه حتى يُربطوا بنفس الحساب.",
        "6) بعد التعبئة احفظ الملف وارفعه من صفحة «الحسابات ← استيراد من إكسل».",
    ]
    for i, line in enumerate(lines, start=3):
        instructions.cell(row=i, column=1, value=line)
    instructions.column_dimensions["A"].width = 110

    return wb


def _find_category(conn, name):
    return q1(conn, "SELECT * FROM categories WHERE active=1 AND TRIM(name)=TRIM(?)", (name,))


def _find_branch(conn, name):
    return q1(conn, "SELECT * FROM branches WHERE active=1 AND TRIM(name)=TRIM(?)", (name,))


def _find_group(conn, name, branch_id):
    return q1(conn, "SELECT * FROM groups_ WHERE active=1 AND TRIM(name)=TRIM(?) AND branch_id=?", (name, branch_id))


def parse_workbook(conn, file_stream):
    """Returns (valid_rows, errors). valid_rows is [] if errors is non-empty
    — nothing should be created unless the whole file passed validation."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(file_stream, data_only=True)
    except Exception:
        return [], ["تعذّر فتح الملف — تأكد أنه ملف إكسل (.xlsx) صالح ولم يتلف أثناء الرفع"]

    ws = None
    for sheet in wb.worksheets:
        if sheet.title == "بيانات اللاعبين":
            ws = sheet
            break
    if ws is None:
        ws = wb.worksheets[0]

    errors = []
    rows = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        cells = list(row) + [None] * (9 - len(row))
        first = _norm(cells[COL_FIRST])
        last = _norm(cells[COL_LAST])
        if not first and not last:
            continue  # fully blank row — skip silently
        if first.startswith("مثال"):
            continue  # the greyed-out example row — skip silently

        dob_raw = cells[COL_DOB]
        gender_raw = _norm(cells[COL_GENDER])
        category_raw = _norm(cells[COL_CATEGORY])
        branch_raw = _norm(cells[COL_BRANCH])
        group_raw = _norm(cells[COL_GROUP])
        parent_name = _norm(cells[COL_PARENT_NAME]) or None
        parent_phone = _norm(cells[COL_PARENT_PHONE]) or None

        row_errors = []
        if not first:
            row_errors.append("الاسم الأول فارغ")
        if not last:
            row_errors.append("اسم العائلة فارغ")

        dob = None
        if isinstance(dob_raw, (datetime, date)):
            dob = dob_raw.date().isoformat() if isinstance(dob_raw, datetime) else dob_raw.isoformat()
        elif _norm(dob_raw):
            try:
                dob = datetime.strptime(_norm(dob_raw), "%Y-%m-%d").date().isoformat()
            except ValueError:
                row_errors.append(f"تاريخ الميلاد '{dob_raw}' غير صالح (الصيغة المطلوبة: سنة-شهر-يوم)")
        else:
            row_errors.append("تاريخ الميلاد فارغ")

        gender = GENDER_MAP.get(gender_raw)
        if not gender:
            row_errors.append(f"الجنس '{gender_raw}' غير معروف (اكتب ذكر أو أنثى)")

        category = _find_category(conn, category_raw) if category_raw else None
        if not category_raw:
            row_errors.append("الفئة فارغة")
        elif not category:
            row_errors.append(f"الفئة '{category_raw}' غير موجودة في النظام")

        branch = _find_branch(conn, branch_raw) if branch_raw else None
        if not branch_raw:
            row_errors.append("الفرع فارغ")
        elif not branch:
            row_errors.append(f"الفرع '{branch_raw}' غير موجود في النظام")

        group = None
        if group_raw and branch:
            group = _find_group(conn, group_raw, branch["id"])
            if not group:
                row_errors.append(f"المجموعة '{group_raw}' غير موجودة في الفرع '{branch_raw}'")

        if row_errors:
            errors.append(f"الصف {row_idx}: " + " — ".join(row_errors))
            continue

        rows.append({
            "row": row_idx, "first_name": first, "last_name": last, "dob": dob, "gender": gender,
            "category_id": category["id"], "category_name": category["name"],
            "branch_id": branch["id"], "group_id": group["id"] if group else None,
            "coach_id": group["coach_id"] if group else None,
            "parent_name": parent_name, "parent_phone": parent_phone,
        })

    if not rows and not errors:
        errors.append("لم يتم العثور على أي صف بيانات في الملف — تأكد أنك عبّأت الصفوف بدءًا من الصف الثالث")

    return (rows, []) if not errors else ([], errors)


def import_players(conn, rows, unified_password, admin_user_id):
    """Creates every validated row: player + (deduped) parent account, both
    forced to change the shared/random password on first login. Returns a
    summary dict plus the created players grouped by category, ready for
    the credentials PDF."""
    next_n = q1(conn, "SELECT COUNT(*) c FROM players")["c"] + 1
    created = []
    for r in rows:
        code = f"FOUQ-{9000 + next_n}"
        next_n += 1

        parent_id = None
        if r["parent_name"]:
            existing_parent = find_parent_by_phone(conn, r["parent_phone"])
            if existing_parent:
                parent_id = existing_parent["id"]
            else:
                puid, _pw = create_user_account(conn, r["parent_name"], "PARENT", phone=r["parent_phone"])
                parent_id = ex(conn, "INSERT INTO parents(user_id, name, phone) VALUES (?,?,?)",
                                (puid, r["parent_name"], r["parent_phone"]))

        player_uid = create_user_account_with_password(
            conn, f"{r['first_name']} {r['last_name']}", "PLAYER", unified_password,
        )
        pid = ex(conn, """INSERT INTO players(player_code, user_id, first_name, last_name, dob, gender,
                          category_id, branch_id, group_id, coach_id, player_type, join_date, referral_code,
                          onboarding_json)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (code, player_uid, r["first_name"], r["last_name"], r["dob"], r["gender"],
                  r["category_id"], r["branch_id"], r["group_id"], r["coach_id"], "FOUQ",
                  date.today().isoformat(), f"REF-{code}",
                  '{"account_created": true, "parent_linked": true, "group_assigned": true, "qr_issued": true}'))
        if parent_id:
            ex(conn, "INSERT INTO parent_players(parent_id, player_id) VALUES (?,?)", (parent_id, pid))
        ex(conn, "INSERT INTO points_wallets(player_id, balance) VALUES (?,0)", (pid,))

        audit_log(conn, admin_user_id, "BULK_IMPORT_PLAYER", "players", pid,
                  after={"first_name": r["first_name"], "last_name": r["last_name"], "category": r["category_name"]},
                  reason="استيراد جماعي من ملف إكسل")

        created.append({"id": pid, "code": code, "first_name": r["first_name"], "last_name": r["last_name"],
                         "category_name": r["category_name"]})

    return created
