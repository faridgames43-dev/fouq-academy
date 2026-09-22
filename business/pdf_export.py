"""Pure-Python PDF report generation on official FOUQ Academy letterhead.

Render's free-tier web service only runs `pip install -r requirements.txt`
(no Node.js, no system packages), so this deliberately avoids any
Node/Playwright/wkhtmltopdf/WeasyPrint approach and instead builds PDFs
directly with reportlab (already a dependency). Arabic text needs explicit
shaping (joining letters) and bidi reordering before reportlab can draw it
correctly, so `arabic_reshaper` + `python-bidi` are used for every piece of
Arabic text drawn on the page.

The Arabic TTF font (Amiri, SIL Open Font License — see
static/fonts/Amiri-OFL.txt) is bundled directly in the repo under
static/fonts/, so rendering never depends on downloading anything at
runtime — no flaky CDN, no "first request after a cold start is slow",
and no silent fallback to a Latin font that can't draw Arabic glyphs at
all (which is what previously caused Arabic text to render as solid
boxes whenever the font download failed). A legacy download path is kept
as a last-resort fallback only for the (very unlikely) case the bundled
file is ever missing.
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
BUNDLED_FONT_DIR = os.path.join(BASE_DIR, "static", "fonts")
FONT_DIR = os.path.join("/tmp", "fouq_fonts")  # only used by the legacy download fallback

FONT_REGULAR = "FoqArabic"
FONT_BOLD = "FoqArabic-Bold"

NAVY = colors.HexColor("#0b1f35")
NAVY_DARK = colors.HexColor("#051323")
GOLD = colors.HexColor("#cf931e")
GOLD_LIGHT = colors.HexColor("#e8b94f")
TEXT_DIM = colors.HexColor("#5b6478")
BORDER = colors.HexColor("#e6e9f2")
CARD_BG = colors.HexColor("#f7f9fc")

LOGIN_URL = "https://fouq-academy.onrender.com/login"

_FONT_READY = {"ok": False}
_LOGO_CACHE = {}
_QR_CACHE = {}

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
    """Register the Arabic TTF fonts once per process. Safe to call repeatedly.

    Prefers the font bundled in the repo (static/fonts/) — deterministic,
    no network needed. Only if that's somehow missing does it fall back to
    downloading a copy (legacy behavior, kept as a safety net)."""
    if _FONT_READY["ok"]:
        return _FONT_READY["regular"], _FONT_READY["bold"]

    bundled_reg = os.path.join(BUNDLED_FONT_DIR, "Amiri-Regular.ttf")
    bundled_bold = os.path.join(BUNDLED_FONT_DIR, "Amiri-Bold.ttf")
    got_regular = os.path.exists(bundled_reg) and os.path.getsize(bundled_reg) > 1000
    got_bold = os.path.exists(bundled_bold) and os.path.getsize(bundled_bold) > 1000
    reg_path, bold_path = bundled_reg, bundled_bold

    if not (got_regular and got_bold):
        reg_path = os.path.join(FONT_DIR, "Amiri-Regular.ttf")
        bold_path = os.path.join(FONT_DIR, "Amiri-Bold.ttf")
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


def _get_login_qr(url=LOGIN_URL):
    """A small QR code encoding the login page URL, generated once and
    reused across every card/page — lets a parent just scan-to-open the
    site on their phone instead of typing the address."""
    if url in _QR_CACHE:
        return _QR_CACHE[url]
    reader = None
    try:
        import qrcode
        from reportlab.lib.utils import ImageReader
        img = qrcode.make(url, border=1, box_size=6)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        reader = ImageReader(buf)
    except Exception:
        reader = None
    _QR_CACHE[url] = reader
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


def _draw_credential_card(c, x, y_top, w, h, regular, bold, player_name, username, password):
    """One bordered 'ticket' for a single player: QR to the login page on
    one side, name + username + password on the other, a gold accent bar
    marking the brand — designed to be cut along the dashed line below each
    card and handed to one family at a time."""
    y_bot = y_top - h
    c.setFillColor(CARD_BG)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.roundRect(x, y_bot, w, h, 7, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.roundRect(x + w - 5, y_bot, 5, h, 2.5, fill=1, stroke=0)

    qr_size = h - 22
    qr = _get_login_qr()
    if qr:
        c.drawImage(qr, x + 14, y_bot + (h - qr_size) / 2, width=qr_size, height=qr_size,
                    preserveAspectRatio=True, mask="auto")

    text_right = x + w - 18
    c.setFillColor(NAVY)
    c.setFont(bold, 13)
    c.drawRightString(text_right, y_top - 24, ar(player_name))

    label_w = 78
    for i, (label, value) in enumerate((("اسم المستخدم", username), ("كلمة المرور", password))):
        row_y = y_top - 46 - i * 24
        c.setFillColor(TEXT_DIM)
        c.setFont(regular, 9.5)
        c.drawRightString(text_right, row_y, ar(label))
        pill_w = 108
        pill_x = text_right - label_w - pill_w
        c.setFillColor(colors.white)
        c.setStrokeColor(GOLD_LIGHT)
        c.roundRect(pill_x, row_y - 12, pill_w, 18, 4, fill=1, stroke=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(pill_x + pill_w / 2, row_y - 6.5, value)


def build_credentials_pdf(created_players, unified_password):
    """One handout PDF, players sorted alphabetically by name (spanning as
    many pages as needed — never crammed), one credential 'ticket' card
    per newly-imported player (name / login username / initial shared
    password + a QR straight to the login page) — meant to be printed and
    cut along the dashed lines, one card per family. created_players is
    the list returned by business.bulk_import.import_players()."""
    players = sorted(created_players, key=lambda p: (p["first_name"], p["last_name"]))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    regular, bold = ensure_fonts()

    def new_page(subtitle):
        yy = _draw_letterhead(c, width, height, "بيانات الدخول لأولياء الأمور", subtitle)
        c.setFillColor(TEXT_DIM)
        c.setFont(regular, 9.5)
        note_lines = [
            f"رابط الدخول: fouq-academy.onrender.com (أو مسح رمز QR على كل بطاقة)",
            f"كلمة المرور المبدئية لكل اللاعبين أدناه: {unified_password} — سيُطلب تغييرها عند أول دخول.",
            "راجعوا الدليل المرفق «دليل تسجيل الدخول» لشرح خطوة بخطوة بالصور.",
        ]
        for line in note_lines:
            c.drawRightString(width - 40, yy, ar(line))
            yy -= 14
        yy = _section_title(c, yy - 6, width, f"اللاعبون مرتبون أبجديًا ({len(players)} لاعب)")
        return yy

    y = new_page("أكاديمية فوق — قصّ كل بطاقة وتسليمها لعائلتها فقط")

    card_h = 92
    card_gap = 14

    for p in players:
        if y - card_h < 60:
            _draw_footer(c, width)
            c.showPage()
            y = new_page("تابع")

        _draw_credential_card(c, 40, y, width - 80, card_h, regular, bold,
                               f"{p['first_name']} {p['last_name']}", p["code"], unified_password)
        y -= card_h + 6
        c.setDash(3, 3)
        c.setStrokeColor(colors.HexColor("#c7cee0"))
        c.line(40, y, width - 40, y)
        c.setDash()
        y -= card_gap

    _draw_footer(c, width)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


LOGIN_GUIDE_STEPS = [
    ("١", "افتح رابط الموقع", "افتح رابط تسجيل الدخول من المتصفح، أو امسح رمز QR الموجود على بطاقة الدخول الخاصة باللاعب."),
    ("٢", "أدخل اسم المستخدم", "اكتب رمز اللاعب (مثل FOUQ-0000) الموجود على البطاقة في خانة «البريد الإلكتروني أو رقم الجوال»."),
    ("٣", "أدخل كلمة المرور", "اكتب كلمة المرور المبدئية المطبوعة على البطاقة، ثم اضغط زر «دخول»."),
    ("٤", "غيّر كلمة المرور", "عند أول دخول سيُطلب تغيير كلمة المرور المبدئية إلى كلمة مرور خاصة — أدخل الحالية ثم الجديدة مرتين."),
    ("٥", "احفظ كلمة المرور الجديدة", "اضغط «حفظ كلمة المرور الجديدة» — بعدها لن تُستخدم كلمة المرور المبدئية مرة أخرى."),
    ("٦", "ادخل لحسابك", "سيظهر لك تنبيه بنجاح العملية وتنتقل مباشرة إلى لوحة اللاعب، حيث تجد رصيد الحصص والنقاط وتطورك."),
]


def build_login_guide_pdf(screenshot_paths):
    """Generic, reusable 'how to log in' walkthrough — contains NO player
    names or real secrets, only a fixed set of numbered steps each paired
    with a real screenshot of the actual login flow (captured against a
    disposable demo account). Meant to be handed out / re-downloaded once
    and reused for every family, independent of any specific player's
    credentials card. screenshot_paths: list of 6 local PNG file paths in
    the same order as LOGIN_GUIDE_STEPS."""
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    regular, bold = ensure_fonts()

    def new_page(subtitle="أكاديمية فوق"):
        return _draw_letterhead(c, width, height, "دليل تسجيل الدخول لحساب اللاعب", subtitle)

    y = new_page()
    c.setFillColor(TEXT_DIM)
    c.setFont(regular, 9.5)
    import textwrap
    intro_lines = textwrap.wrap(
        "هذا الدليل عام وقابل لإعادة الاستخدام لكل العائلات — بيانات الدخول (اسم المستخدم وكلمة "
        "المرور) موجودة فقط على بطاقة اللاعب الخاصة بكم، وليست في هذا الملف.", 78)
    for line in intro_lines:
        c.drawRightString(width - 40, y, ar(line))
        y -= 13
    y -= 12

    col_gap = 24
    col_w = (width - 80 - col_gap) / 2
    img_w = col_w
    img_h = img_w * (760 / 480)
    max_img_h = 300
    if img_h > max_img_h:
        img_h = max_img_h
        img_w = img_h * (480 / 760)

    def draw_step(x, top_y, num, title, desc, img_path):
        badge_r = 13
        c.setFillColor(GOLD)
        c.circle(x + badge_r, top_y - badge_r, badge_r, fill=1, stroke=0)
        c.setFillColor(NAVY_DARK)
        c.setFont(bold, 12)
        c.drawCentredString(x + badge_r, top_y - badge_r - 4, num)

        c.setFillColor(NAVY)
        c.setFont(bold, 11.5)
        c.drawRightString(x + col_w, top_y - 10, ar(title))

        c.setFillColor(TEXT_DIM)
        c.setFont(regular, 8.7)
        text_y = top_y - 26
        import textwrap
        for line in textwrap.wrap(desc, 46):
            c.drawRightString(x + col_w, text_y, ar(line))
            text_y -= 11

        img_top = text_y - 8
        try:
            reader = ImageReader(img_path)
            frame_x = x + (col_w - img_w) / 2
            c.setStrokeColor(BORDER)
            c.setLineWidth(1)
            c.roundRect(frame_x - 3, img_top - img_h - 3, img_w + 6, img_h + 6, 5, fill=0, stroke=1)
            c.drawImage(reader, frame_x, img_top - img_h, width=img_w, height=img_h,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
        return img_top - img_h - 18

    pairs = list(zip(LOGIN_GUIDE_STEPS, screenshot_paths))
    for i in range(0, len(pairs), 2):
        chunk = pairs[i:i + 2]
        if i > 0:
            _draw_footer(c, width)
            c.showPage()
            y = new_page("تابع")
        row_top = y
        x = 40
        for (num, title, desc), img_path in chunk:
            draw_step(x, row_top, num, title, desc, img_path)
            x += col_w + col_gap

    # closing note + QR shortcut to the login page, on the last page
    y2 = 150
    c.setFillColor(NAVY)
    c.roundRect(40, y2 - 90, width - 80, 90, 8, fill=1, stroke=0)
    qr = _get_login_qr()
    if qr:
        c.drawImage(qr, width - 40 - 74, y2 - 82, width=74, height=74, preserveAspectRatio=True, mask="auto")
    c.setFillColor(colors.white)
    c.setFont(bold, 11.5)
    c.drawRightString(width - 130, y2 - 28, ar("امسح الرمز لفتح صفحة الدخول مباشرة"))
    c.setFont(regular, 9)
    c.setFillColor(colors.HexColor("#cfd9ea"))
    c.drawRightString(width - 130, y2 - 46, ar("أو زوروا: " + LOGIN_URL.replace("https://", "")))
    c.drawRightString(width - 130, y2 - 62, ar("لأي استفسار تواصلوا مع إدارة الأكاديمية."))

    _draw_footer(c, width)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _draw_roster_block(c, x, y_top, w, h, regular, bold, seq_num, player_name, username, password, qr_reader):
    """One player's record for the full-roster export: name (most
    prominent), username, a temporary-password field left as an empty
    fillable box whenever the real value isn't known (accounts created
    before this export existed have no recoverable plaintext password —
    passwords are stored as one-way hashes, so nothing is guessed or
    invented here), and the player's own QR code at a size that's easy to
    scan. Never split across a page break by the caller."""
    y_bot = y_top - h
    c.setFillColor(CARD_BG)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.roundRect(x, y_bot, w, h, 8, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.roundRect(x + w - 5, y_bot, 5, h, 2.5, fill=1, stroke=0)

    qr_size = min(h - 24, 100)
    if qr_reader:
        c.drawImage(qr_reader, x + 16, y_bot + (h - qr_size) / 2, width=qr_size, height=qr_size,
                     preserveAspectRatio=True, mask="auto")

    text_right = x + w - 20
    badge_r = 12
    badge_cx = text_right - badge_r
    top_y = y_top - 24
    c.setFillColor(GOLD)
    c.circle(badge_cx, top_y, badge_r, fill=1, stroke=0)
    c.setFillColor(NAVY_DARK)
    c.setFont(bold, 10.5)
    c.drawCentredString(badge_cx, top_y - 3.5, str(seq_num))

    c.setFillColor(NAVY)
    c.setFont(bold, 15)
    c.drawRightString(text_right - badge_r * 2 - 10, top_y + 5, ar(player_name))

    # Label-above-field layout (not side-by-side): a label's rendered
    # width can't be predicted precisely for shaped Arabic text, so a
    # side-by-side box positioned by estimated text width risks the box
    # painting over part of the label when the estimate runs short. Each
    # field gets its own full-width row instead, which is immune to that.
    field_w = w - 40 - qr_size - 16
    field_x = text_right - field_w
    row_y = top_y - 32

    c.setFillColor(TEXT_DIM)
    c.setFont(regular, 9.5)
    c.drawRightString(text_right, row_y, ar("اسم المستخدم"))
    row_y -= 16
    c.setFillColor(colors.white)
    c.setStrokeColor(GOLD_LIGHT)
    c.roundRect(field_x, row_y - 14, field_w, 20, 4, fill=1, stroke=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(field_x + field_w / 2, row_y - 7.5, username or "")

    row_y -= 34
    c.setFillColor(TEXT_DIM)
    c.setFont(regular, 9.5)
    c.drawRightString(text_right, row_y, ar("كلمة المرور المؤقتة"))
    row_y -= 16
    c.setFillColor(colors.white)
    c.setStrokeColor(BORDER)
    c.roundRect(field_x, row_y - 14, field_w, 20, 4, fill=1, stroke=1)
    if password:
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(field_x + field_w / 2, row_y - 7.5, password)
    # else: left intentionally empty (fillable by hand) — no placeholder text


def build_full_roster_pdf(players):
    """Full-roster export: every player already in the system whose
    player_type is FOUQ (i.e. genuinely 'أكاديمية فوق' members — players
    with player_type LEGACY belong to the previous Tawasol club and are
    excluded), sorted alphabetically by name, one record per player, never
    split across a page break. players: list of dicts with first_name,
    last_name, code (player_code / login username), and optionally
    password (left blank/None when not known — never invented)."""
    from business.barcode import render_qr
    from reportlab.lib.utils import ImageReader

    ordered = sorted(players, key=lambda p: (p["first_name"], p["last_name"]))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    regular, bold = ensure_fonts()

    def new_page(subtitle):
        yy = _draw_letterhead(c, width, height, "بيانات لاعبي أكاديمية فوق", subtitle)
        c.setFillColor(TEXT_DIM)
        c.setFont(regular, 9)
        c.drawRightString(width - 40, yy, ar(
            "قائمة موثّقة من نظام أكاديمية فوق فقط — لاعبو أكاديمية فوق (FOUQ) حصرًا، بدون أي لاعب من جهة أخرى."))
        yy -= 22
        return yy

    y = new_page(f"{len(ordered)} لاعب — مرتبون أبجديًا")

    block_h = 148
    block_gap = 12

    for i, p in enumerate(ordered, start=1):
        if y - block_h < 60:
            _draw_footer(c, width)
            c.showPage()
            y = new_page("تابع")

        code = p.get("code") or ""
        qr_reader = None
        if code:
            try:
                qr_reader = ImageReader(io.BytesIO(render_qr(code)))
            except Exception:
                qr_reader = None

        _draw_roster_block(c, 40, y, width - 80, block_h, regular, bold, i,
                            f"{p['first_name']} {p['last_name']}", code, p.get("password"), qr_reader)
        y -= block_h + block_gap

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
