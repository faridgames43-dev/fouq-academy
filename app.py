import os
from flask import Flask, session, redirect, url_for, g, request, render_template
from db import get_conn, q1, init_db, migrate_db, DB_PATH
from jinja2 import ChoiceLoader, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(os.path.join(BASE_DIR, "blueprints", "templates"))])
    app.secret_key = os.environ.get("FOUQ_SECRET_KEY", "fouq-academy-dev-secret-change-in-production-2026")
    app.config["JSON_AS_ASCII"] = False
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30MB safety cap (assignment video uploads, etc.)

    if not os.path.exists(DB_PATH):
                from seed import main as seed_main; seed_main()
    migrate_db()

    FORCED_RESET_ALLOWED_ENDPOINTS = {"auth.change_password", "auth.logout", "static"}

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid:
            conn = get_conn()
            g.user = q1(conn, "SELECT * FROM users WHERE id=?", (uid,))
            conn.close()
        if g.user and g.user.get("must_reset_password") and request.endpoint not in FORCED_RESET_ALLOWED_ENDPOINTS:
            return redirect(url_for("auth.change_password"))

    @app.context_processor
    def inject_globals():
        from business.notifications import unread_count
        unread = 0
        if g.get("user"):
            conn = get_conn()
            unread = unread_count(conn, g.user["id"])
            conn.close()
        return {"current_user": g.get("user"), "unread_notifications": unread,
                "academy_name": "أكاديمية فوق", "academy_tagline": "رايحين فوق!"}

    from blueprints.auth import bp as auth_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.players import bp as players_bp
    from blueprints.subscriptions_bp import bp as subscriptions_bp
    from blueprints.attendance_bp import bp as attendance_bp
    from blueprints.renewals_bp import bp as renewals_bp
    from blueprints.assessments_bp import bp as assessments_bp
    from blueprints.levels_bp import bp as levels_bp
    from blueprints.points_bp import bp as points_bp
    from blueprints.rewards_bp import bp as rewards_bp
    from blueprints.reports_bp import bp as reports_bp
    from blueprints.settings_bp import bp as settings_bp
    from blueprints.crm_bp import bp as crm_bp
    from blueprints.retention_bp import bp as retention_bp
    from blueprints.audit_bp import bp as audit_bp
    from blueprints.search_bp import bp as search_bp
    from blueprints.parent_bp import bp as parent_bp
    from blueprints.player_bp import bp as player_bp
    from blueprints.notifications_bp import bp as notifications_bp
    from blueprints.assignments_bp import bp as assignments_bp
    from blueprints.accounts_bp import bp as accounts_bp

    for bp in [auth_bp, dashboard_bp, players_bp, subscriptions_bp, attendance_bp, renewals_bp,
               assessments_bp, levels_bp, points_bp, rewards_bp, reports_bp, settings_bp, crm_bp,
               retention_bp, audit_bp, search_bp, parent_bp, player_bp, notifications_bp, assignments_bp,
               accounts_bp]:
        app.register_blueprint(bp)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="لا تملك صلاحية الوصول لهذه الصفحة"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="الصفحة غير موجودة"), 404

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)
