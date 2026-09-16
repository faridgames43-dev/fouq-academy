import json
from db import ex


def log(conn, user_id, action, entity_type, entity_id=None, before=None, after=None, reason=None):
    ex(
        conn,
        """INSERT INTO audit_logs(user_id, action, entity_type, entity_id, before_json, after_json, reason)
           VALUES (?,?,?,?,?,?,?)""",
        (
            user_id,
            action,
            entity_type,
            entity_id,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            reason,
        ),
    )
