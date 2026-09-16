"""Minimal, dependency-free Code39 barcode renderer (no external barcode
lib is installed in this environment). Code39 is a real, widely-supported
1D symbology - any standard USB/Bluetooth barcode scanner (which behaves
as a keyboard-wedge device) can read a printed version of this image, and
the same player_code value is what the on-screen scan input matches
against, so the "scan" workflow is genuinely functional end to end."""
from PIL import Image, ImageDraw, ImageFont
import io

# Code 39 pattern table: each char -> 9 widths (alternating bar/space), 'N'=narrow,'W'=wide
CODE39_PATTERNS = {
    '0': "NNNWWNWNN", '1': "WNNWNNNNW", '2': "NNWWNNNNW", '3': "WNWWNNNNN",
    '4': "NNNWWNNNW", '5': "WNNWWNNNN", '6': "NNWWWNNNN", '7': "NNNWNNWNW",
    '8': "WNNWNNWNN", '9': "NNWWNNWNN", 'A': "WNNNNWNNW", 'B': "NNWNNWNNW",
    'C': "WNWNNWNNN", 'D': "NNNNWWNNW", 'E': "WNNNWWNNN", 'F': "NNWNWWNNN",
    'G': "NNNNNWWNW", 'H': "WNNNNWWNN", 'I': "NNWNNWWNN", 'J': "NNNNWWWNN",
    'K': "WNNNNNNWW", 'L': "NNWNNNNWW", 'M': "WNWNNNNWN", 'N': "NNNNWNNWW",
    'O': "WNNNWNNWN", 'P': "NNWNWNNWN", 'Q': "NNNNNNWWW", 'R': "WNNNNNWWN",
    'S': "NNWNNNWWN", 'T': "NNNNWNWWN", 'U': "WWNNNNNNW", 'V': "NWWNNNNNW",
    'W': "WWWNNNNNN", 'X': "NWNNWNNNW", 'Y': "WWNNWNNNN", 'Z': "NWWNWNNNN",
    '-': "NWNNNNWNW", '.': "WWNNNNWNN", ' ': "NWWNNNWNN", '*': "NWNNWNWNN",
}


def render_code39(code: str, width=420, height=110) -> bytes:
    text = code.upper()
    full = f"*{text}*"
    narrow = 3
    wide = narrow * 2.5
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    total_units = 0
    seq = []
    for ch in full:
        pattern = CODE39_PATTERNS.get(ch, CODE39_PATTERNS['-'])
        seq.append(pattern)
        total_units += sum(wide if p == 'W' else narrow for p in pattern) + narrow  # inter-char gap

    scale = (width - 20) / total_units if total_units else 1
    x = 10
    bar_top, bar_bottom = 10, 75
    for pattern in seq:
        is_bar = True
        for p in pattern:
            w = (wide if p == 'W' else narrow) * scale
            if is_bar:
                draw.rectangle([x, bar_top, x + w, bar_bottom], fill="black")
            x += w
            is_bar = not is_bar
        x += narrow * scale  # inter-character gap

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.text((width / 2, 88), text, fill="black", anchor="mm", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
