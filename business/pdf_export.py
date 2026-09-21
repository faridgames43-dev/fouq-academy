"""Pure-Python PDF report generation on official FOUQ Academy letterhead.

Render's free-tier web service only runs `pip install -r requirements.txt`
(no Node.js, no system packages), so this deliberately avoids any
Node/Playwright/wkhtmltopdf/WeasyPrint approach and instead builds PDFs
directly with reportlab (already a dependency). Arabic text needs explicit
shaping (joining letters) and bidi reordering before reportlab can draw it
correctly, so `arabic_reshaper` + `python-bidi` are used for every piece of
Arabic text drawn on the page.

The Arabic TTF font itself is not committed to the repo (binary fonts can't
be committed through this project's text-only deployment pipeline) — it is
downloaded once and cached on first use, then reused for the life of the
running process. If the download ever fails (e.g. no network), Arabic text
falls back to a Latin font, which will render blank glyphs; the PDF still
generates instead of crashing.
"""
import io
import os
import urllib.request

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Table, TableStyle

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join("/tmp", "fouq_fonts")

FONT_REGULAR = "FoqArabic"
FONT_BOLD = "FoqArabic-Bold"

NAVY = colors.HexColor("#0b1f35")
NAVY_DARK = colors.HexColor("#051323")
GOLD = colors.HexColor("#cf931e")
TEXT_DIM = colors.HexColor("#5b6478")
BORDER = colors.HexColor("#e6e9f2")

_FONT_READY = {"ok": False}
_LOGO_CACHE = {}

FONT_SOURCES = [
    ("https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/amiri/Amiri-Regular.ttf",
     "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/amiri/Amiri-Bold.ttf"),
    ("https://raw.githubusercontent.com/google/fonts/main/ofl/amiri/Amiri-Regular.ttf",
     "https://raw.githubusercontent.com/google/fonts/main/ofl/amiri/Amiri-Bold.ttf"),
]


def _download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return True
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return os.path.getsize(dest) > 1000
    except Exception:
        return False


def ensure_fonts():
    """Register the Arabic TTF fonts once per process. Safe to call repeatedly."""
    if _FONT_READY["ok"]:
        return _FONT_READY["regular"], _FONT_READY["bold"]
    reg_path = os.path.join(FONT_DIR, "Amiri-Regular.ttf")
    bold_path = os.path.join(FONT_DIR, "Amiri-Bold.ttf")
    got_regular = False
    got_bold = False
    for reg_url, bold_url in FONT_SOURCES:
        if not got_regular:
            got_regular = _download(reg_url, reg_path)
        if not got_bold:
            got_bold = _download(bold_url, bold_path)
        if got_regular and got_bold:
            break
    try:
        if got_regular and FONT_REGULAR not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_REGULAR, reg_path))
        if got_bold and FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold_path))
    except Exception:
        got_regular = got_bold = False
    _FONT_READY["ok"] = True
    _FONT_READY["regular"] = FONT_REGULAR if got_regular else "Helvetica"
    _FONT_READY["bold"] = FONT_BOLD if got_bold else "Helvetica-Bold"
    return _FONT_READY["regular"], _FONT_READY["bold"]


def ar(text):
    """Shape + bidi-reorder Arabic text so reportlab draws it correctly."""
    if text is None:
        return ""
    text = str(text)
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def fmt_val(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.1f}"
    return str(v)


COLUMN_LABELS = {
    "id": "الرقم", "first_name": "الاسم الأول", "last_name": "اسم العائلة",
    "player_code": "كود اللاعب", "sub_status": "حالة الاشتراك",
    "regular_remaining": "حصص أساسية", "comp_remaining": "حصص تعويضية",
    "bonus_remaining": "حصص إضافية", "nearest_expiry": "أقرب انتهاء",
    "last_attendance": "آخر حضور", "session_date": "تاريخ الحصة",
    "start_time": "الوقت", "group_name": "المجموعة", "status": "الحالة",
    "package_name_snapshot": "الباقة", "start_date": "من", "end_date": "إلى",
    "price": "السعر", "discount": "الخصم", "paid_amount": "المدفوع",
    "payment_method": "طريقة الدفع", "payment_status": "حالة الدفع",
    "invoice_ref": "الفاتورة", "created_at": "تاريخ الإنشاء", "month": "الشهر",
    "revenue": "الإيراد", "subs": "عدد الاشتراكات", "name": "الاسم",
    "groups_count": "عدد المجموعات", "players_count": "عدد اللاعبين",
    "avg_attendance": "معدل الحضور", "assessments_done": "تقييمات منجزة",
}


def _get_small_logo():
    """Downscale the source logo once per process so every generated PDF
    doesn't embed the full ~1MB source image just to show it at 54x54."""
    if "reader" in _LOGO_CACHE:
        return _LOGO_CACHE["reader"]
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.png")
    reader = None
    try:
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        if os.path.exists(logo_path):
            img = Image.open(logo_path).convert("RGBA")
            img.thumbnail((160, 160))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            reader = ImageReader(buf)
    except Exception:
        reader = None
    _LOGO_CACHE["reader"] = reader
    return reader


def _draw_letterhead(c, width, height, title, subtitle=None):
    regular, bold = ensure_fonts()
    band_h = 92
    c.setFillColor(NAVY_DARK)
    c.rect(0, height - band_h, width, band_h, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.rect(0, height - band_h - 4, width, 4, fill=1, stroke=0)

    try:
        from reportlab.lib.utils import ImageReader
        logo_reader = _get_small_logo()
        if logo_reader:
            c.drawImage(logo_reader, width - 40 - 54, height - band_h + 19, width=54, height=54,
                         preserveAspectRatio=True, mask="auto")
    except Exception:
        pass

    c.setFillColor(colors.white)
    c.setFont(bold, 18)
    c.drawRightString(width - 110, height - 38, ar("أكاديمية فوق"))
    c.setFont(regular, 10)
    c.setFillColor(GOLD)
    c.drawRightString(width - 110, height - 54, ar("رايحين فوق! ↑"))

    c.setFillColor(colors.white)
    c.setFont(bold, 13)
    c.drawString(40, height - 38, ar(title))
    if subtitle:
        c.setFont(regular, 9.5)
        c.setFillColor(colors.HexColor("#cfd9ea"))
        c.drawString(40, height - 54, ar(subtitle))

    return height - band_h - 26


def _draw_footer(c, width, generated_at=None):
    from datetime import date
    c.setFillColor(BORDER)
    c.setLineWidth(1)
    c.line(40, 34, width - 40, 34)
    regular, _ = ensure_fonts()
    c.setFillColor(TEXT_DIM)
    c.setFont(regular, 8)
    c.drawString(40, 22, ar(f"تم إنشاؤه بتاريخ {generated_at or date.today().isoformat()} — أكاديمية فوق"))
    c.drawRightString(width - 40, 22, ar("مستند رسمي صادر آليًا من نظام أكاديمية فوق"))


def _kv_table(c, y, width, rows, col_widths=None):
    """rows: list of (label, value, label2, value2, ...) tuples, given in
    natural reading order (the first pair is meant to be read first).

    reportlab always draws column 0 on the left regardless of the text
    inside it, so for Arabic (read right-to-left) we must reverse the
    column order before building the table: that puts the first pair on
    the right (where an Arabic reader looks first) and preserves each
    label immediately next to its own value."""
    regular, bold = ensure_fonts()
    n = len(rows[0])
    data = []
    for row in rows:
        rtl_row = list(row)[::-1]
        data.append([ar(fmt_val(v)) for v in rtl_row])
    if not col_widths:
        col_widths = [(width - 80) / n] * n
    else:
        col_widths = list(col_widths)[::-1]
    t = Table(data, colWidths=col_widths)
    style = [
        ("FONT", (0, 0), (-1, -1), regular, 9.5),
        ("GRID", (0, 0), (-1, -1), 0.6, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # Label columns were originally at even indices (0, 2, ...); after the
    # reversal above they land on the odd indices instead.
    for i in range(1, n, 2):
        style.append(("BACKGROUND", (i, 0), (i, -1), colors.HexColor("#f4f6fb")))
    t.setStyle(TableStyle(style))
    tw, th = t.wrapOn(c, width - 80, 800)
    t.drawOn(c, 40, y - th)
    return y - th - 16


def _section_title(c, y, width, text):
    regular, bold = ensure_fonts()
    c.setFillColor(NAVY)
    c.roundRect(40, y - 20, width - 80, 22, 4, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(bold, 10.5)
    c.drawRightString(width - 48, y - 15, ar(text))
    return y - 32


def build_generic_report_pdf(title, rows):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    regular, bold = ensure_fonts()
    y = _draw_letterhead(c, width, height, title)

    if not rows:
        c.setFillColor(TEXT_DIM)
        c.setFont(regular, 11)
        c.drawCentredString(width / 2, y - 30, ar("لا يوجد بيانات لعرضها"))
    else:
        columns = list(rows[0].keys())
        max_cols = 6
        columns = columns[:max_cols]
        # Column order is authored left-to-right in natural reading order
        # (first column = first thing to read). reportlab always draws
        # column 0 on the left, so reverse it for Arabic: the first column
        # then lands on the right, where an Arabic reader looks first.
        columns = columns[::-1]
        header = [ar(COLUMN_LABELS.get(col, col)) for col in columns]
        table_data = [header]
        for r in rows[:60]:
            table_data.append([ar(fmt_val(r.get(col))) for col in columns])
        col_w = (width - 80) / len(columns)
        t = Table(table_data, colWidths=[col_w] * len(columns), repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), regular, 8.5),
            ("FONT", (0, 0), (-1, 0), bold, 9),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfe")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        tw, th = t.wrapOn(c, width - 80, height)
        if th > y - 60:
            t.drawOn(c, 40, max(60, y - th))
        else:
            t.drawOn(c, 40, y - th - 6)

    _draw_footer(c, width)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def build_monthly_report_pdf(data):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = _draw_letterhead(c, width, height, "التقرير الشهري للإدارة", "أكاديمية فوق")

    rows = [
        ("اللاعبون النشطون", data.get("active_players"), "لاعبون جدد (30 يوم)", data.get("new_players")),
        ("الحضور اليوم", data.get("attendance_today"), "الغياب اليوم", data.get("absence_today")),
        ("اشتراكات تنتهي قريبًا", data.get("expiring_soon"), "اشتراكات منتهية", data.get("expired")),
        ("حصص مستحقة قائمة", data.get("outstanding_compensation"), "تجديدات (30 يوم)", data.get("renewals_30d")),
        ("إيراد الشهر الحالي", f"{data.get('revenue_month', 0):,.0f} ر.س", "معدل التجديد", f"{data.get('renewal_rate', 0)}٪"),
        ("معدل التسرب (Churn)", f"{data.get('churn', 0)}٪", "معدل الحضور العام", f"{data.get('avg_attendance', 0)}٪"),
        ("متوسط تطور اللاعبين", data.get("avg_development"), "طلبات جوائز معلّقة", data.get("rewards_pending")),
    ]
    y = _section_title(c, y, width, "المؤشرات الرئيسية")
    _kv_table(c, y, width, rows)
    _draw_footer(c, width)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def build_credentials_pdf(created_players, unified_password):
    """One handout PDF, grouped by category, listing each newly-imported
    player's name / login username (their player code) / initial shared
    password — meant to be printed/split and handed to parents. created_players
    is the list returned by business.bulk_import.import_players()."""
    grouped = {}
    for p in created_players:
        grouped.setdefault(p["category_name"], []).append(p)
    for cat in grouped:
        grouped[cat].sort(key=lambda p: (p["first_name"], p["last_name"]))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    regular, bold = ensure_fonts()
    y = _draw_letterhead(c, width, height, "بيانات الدخول لأولياء الأمور",
                          "أكاديمية فوق — يرجى تسليم كل عائلة سطرها الخاص فقط")

    c.setFillColor(TEXT_DIM)
    c.setFont(regular, 9.5)
    note = (f"رابط الدخول: fouq-academy.onrender.com — كلمة المرور المبدئية لكل اللاعبين أدناه: "
            f"{unified_password} (سيُطلب من اللاعب تغييرها بنفسه عند أول تسجيل دخول)")
    c.drawRightString(width - 40, y, ar(note))
    y -= 26

    for cat_name, players in grouped.items():
        est_h = 32 + 22 * (len(players) + 1)
        if y - min(est_h, 140) < 60:
            _draw_footer(c, width)
            c.showPage()
            y = _draw_letterhead(c, width, height, "بيانات الدخول لأولياء الأمور", "تابع")

        y = _section_title(c, y, width, f"الفئة: {cat_name} ({len(players)} لاعب)")
        header = ["اسم اللاعب", "اسم المستخدم (كود اللاعب)", "كلمة المرور"][::-1]
        table_data = [[ar(h) for h in header]]
        for p in players:
            row = [f"{p['first_name']} {p['last_name']}", p["code"], unified_password][::-1]
            table_data.append([ar(v) for v in row])
        col_w = (width - 80) / 3
        t = Table(table_data, colWidths=[col_w] * 3, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), regular, 9.5),
            ("FONT", (0, 0), (-1, 0), bold, 10),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfe")]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        tw, th = t.wrapOn(c, width - 80, height)
        if th > y - 60:
            _draw_footer(c, width)
            c.showPage()
            y = _draw_letterhead(c, width, height, "بيانات الدخول لأولياء الأمور", "تابع")
            tw, th = t.wrapOn(c, width - 80, height)
        t.drawOn(c, 40, y - th)
        y = y - th - 24

    _draw_footer(c, width)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def build_player_report_pdf(ctx):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    player = ctx["player"]
    y = _draw_letterhead(c, width, height, "تقرير تطور اللاعب",
                          f"{player['first_name']} {player['last_name']} — {player['player_code']}")

    level = ctx.get("level")
    y = _section_title(c, y, width, "ملخص اللاعب")
    y = _kv_table(c, y, width, [
        ("الفئة", player.get("category_name") or "-", "الفرع", player.get("branch_name") or "-"),
        ("المجموعة", player.get("group_name") or "-", "المدرب", player.get("coach_name") or "-"),
        ("المستوى الحالي", level["name"] if level else "-", "رصيد فوق", f"{ctx.get('points', 0)} ⭐"),
    ])

    balances = ctx.get("balances", {})
    y = _section_title(c, y, width, "الحصص والحضور")
    y = _kv_table(c, y, width, [
        ("إجمالي الحصص المسجّلة", ctx.get("total_sessions", 0), "نسبة الالتزام", f"{ctx.get('attendance_pct', 0)}٪"),
        ("حاضر", ctx.get("present", 0), "غائب", ctx.get("absent", 0)),
        ("حصص أساسية متبقية", balances.get("REGULAR", 0), "حصص تعويضية متبقية", balances.get("COMPENSATION", 0)),
    ])

    dev = ctx.get("dev") or []
    regular, bold = ensure_fonts()
    y = _section_title(c, y, width, "التطور (المهاري / اللياقي / السلوكي / الانضباط)")
    if dev:
        header = ["العام", "الانضباط", "السلوكي", "اللياقي", "المهاري", "النوع", "التاريخ"]
        table_data = [[ar(h) for h in header]]
        for d in dev[-8:]:
            bd = d.get("breakdown", {})
            table_data.append([
                ar(fmt_val(d.get("overall"))), ar(fmt_val(bd.get("DISCIPLINE"))), ar(fmt_val(bd.get("BEHAVIOR"))),
                ar(fmt_val(bd.get("FITNESS"))), ar(fmt_val(bd.get("SKILL"))),
                ar(fmt_val(d.get("assessment", {}).get("type"))), ar(fmt_val(d.get("assessment", {}).get("assessment_date"))),
            ])
        col_w = (width - 80) / 7
        t = Table(table_data, colWidths=[col_w] * 7, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), regular, 8.5),
            ("FONT", (0, 0), (-1, 0), bold, 9),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        tw, th = t.wrapOn(c, width - 80, height)
        t.drawOn(c, 40, y - th - 4)
        y = y - th - 20
    else:
        c.setFillColor(TEXT_DIM); c.setFont(regular, 10)
        c.drawRightString(width - 40, y - 14, ar("لا يوجد تقييمات مسجّلة بعد"))
        y -= 30

    achievements = ctx.get("achievements") or []
    y = _section_title(c, y, width, "الإنجازات")
    ach_text = " — ".join(f"{a['icon']} {a['name']}" for a in achievements) or "لا توجد إنجازات مسجّلة بعد"
    c.setFillColor(colors.HexColor("#10182b")); c.setFont(regular, 10)
    c.drawRightString(width - 40, y - 14, ar(ach_text[:110]))
    y -= 32

    y = _section_title(c, y, width, "ملاحظة المدرب")
    c.setFillColor(colors.HexColor("#10182b")); c.setFont(regular, 10)
    c.drawRightString(width - 40, y - 14, ar((player.get("notes") or "لا توجد ملاحظات إضافية لهذه الفترة.")[:110]))

    _draw_footer(c, width, ctx.get("generated_at"))
    c.showPage()
    c.save()
    buf.seek(0)
    return buf
