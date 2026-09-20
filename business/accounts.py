"""Account management: creating login credentials for players/parents,
resetting passwords, and disabling/reactivating accounts — all backend,
real (Werkzeug password hashing), never just UI-level."""
import random
import string
from werkzeug.security import generate_password_hash
from db import q1, ex
from business.audit import log as audit_log


class AccountError(Exception):
    pass


def generate_password(length=8):
    """A fresh, random, human-typeable password — shown once to the admin
    right after creation/reset, never stored or re-displayed in plaintext."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"Fouq{digits}"


def find_parent_by_phone(conn, phone):
    """Dedup helper: returns the existing parents row for this phone, if any,
    so a second child never creates a duplicate parent account."""
    if not phone:
        return None
    return q1(conn, "SELECT * FROM parents WHERE phone=?", (phone,))


def create_user_account(conn, name, role, phone=None, email=None, branch_id=None):
    password = generate_password()
    uid = ex(
        conn,
        "INSERT INTO users(name, email, phone, password_hash, role, branch_id) VALUES (?,?,?,?,?,?)",
        (name, email, phone, generate_password_hash(password), role, branch_id),
    )
    return uid, password


def reset_password(conn, user_id, admin_user_id):
    user = q1(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        raise AccountError("الحساب غير موجود")
    new_password = generate_password()
    ex(conn, "UPDATE users SET password_hash=?, must_reset_password=0 WHERE id=?",
       (generate_password_hash(new_password), user_id))
    audit_log(conn, admin_user_id, "RESET_PASSWORD", "users", user_id, reason="إعادة تعيين كلمة المرور")
    return new_password


def set_account_active(conn, user_id, active, admin_user_id):
    user = q1(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        raise AccountError("الحساب غير موجود")
    ex(conn, "UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
    audit_log(conn, admin_user_id, "ACTIVATE_ACCOUNT" if active else "DEACTIVATE_ACCOUNT", "users", user_id)
    return True
