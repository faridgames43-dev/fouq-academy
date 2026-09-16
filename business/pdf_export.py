import os
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
RENDER_SCRIPT = os.path.join(BASE_DIR, "tools", "render_pdf.js")


def render_url_to_pdf(url, filename, session_cookie=None):
    """session_cookie: the raw Flask session cookie value from the requesting
    browser, forwarded to the headless Chromium instance so the print route
    (which stays behind normal login) renders as that same logged-in user."""
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    out_path = os.path.join(EXPORTS_DIR, filename)
    args = ["node", RENDER_SCRIPT, url, out_path]
    if session_cookie:
        args.append(session_cookie)
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"PDF generation failed: {result.stderr}")
    return out_path
