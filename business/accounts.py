"""Account management: creating login credentials for players/parents,
resetting passwords, and disabling/reactivating accounts — all backend,
real (Werkzeug password hashing), never just UI-level."""
import random
import string
from werkzeug.security import generate_password_hash, check_password_hash
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
        "INSERT INTO users(name, email, phone, password_hash, role, branch_id, must_reset_password) VALUES (?,?,?,?,?,?,1)",
        (name, email, phone, generate_password_hash(password), role, branch_id),
    )
    return uid, password


def create_user_account_with_password(conn, name, role, password, phone=None, email=None, branch_id=None):
    """Same as create_user_account(), but with a caller-supplied password
    instead of a randomly generated one — used for bulk import, where every
    player in a batch shares one "unified" password that's printed once on
    the handout PDF. must_reset_password is still forced on, exactly like
    create_user_account(), so the shared password can't be reused after the
    first login."""
    uid = ex(
        conn,
        "INSERT INTO users(name, email, phone, password_hash, role, branch_id, must_reset_password) VALUES (?,?,?,?,?,?,1)",
        (name, email, phone, generate_password_hash(password), role, branch_id),
    )
    return uid


def reset_password(conn, user_id, admin_user_id):
    user = q1(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        raise AccountError("الحساب غير موجود")
    new_password = generate_password()
    ex(conn, "UPDATE users SET password_hash=?, must_reset_password=0 WHERE id=?",
       (generate_password_hash(new_password), user_id))
    audit_log(conn, admin_user_id, "RESET_PASSWORD", "users", user_id, reason="إعادة تعيين كلمة المرور")
    return new_password


def change_own_password(conn, user_id, current_password, new_password):
    """Self-service password change — used both for the forced first-login
    reset (unified/admin-set password) and for a voluntary change later."""
    user = q1(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        raise AccountError("الحساب غير موجود")
    if not check_password_hash(user["password_hash"], current_password):
        raise AccountError("كلمة المرور الحالية غير صحيحة")
    if not new_password or len(new_password) < 6:
        raise AccountError("كلمة المرور الجديدة يجب أن تكون 6 أحرف على الأقل")
    ex(conn, "UPDATE users SET password_hash=?, must_reset_password=0 WHERE id=?",
       (generate_password_hash(new_password), user_id))
    audit_log(conn, user_id, "CHANGE_OWN_PASSWORD", "users", user_id)
    return True


def set_account_active(conn, user_id, active, admin_user_id):
    user = q1(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        raise AccountError("الحساب غير موجود")
    ex(conn, "UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
    audit_log(conn, admin_user_id, "ACTIVATE_ACCOUNT" if active else "DEACTIVATE_ACCOUNT", "users", user_id)
    return True
