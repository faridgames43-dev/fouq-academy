from flask import Blueprint, render_template, request
from db import get_conn, q

bp = Blueprint("search_bp", __name__)


@bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    results = []
    if query:
        conn = get_conn()
        results = q(
            conn,
            """SELECT p.* FROM players p LEFT JOIN parent_players pp ON pp.player_id=p.id
               LEFT JOIN parents pr ON pr.id=pp.parent_id
               WHERE p.first_name LIKE ? OR p.last_name LIKE ? OR p.player_code LIKE ?
               OR pr.phone LIKE ? GROUP BY p.id LIMIT 30""",
            tuple([f"%{query}%"] * 4),
        )
        conn.close()
    return render_template("search_results.html", results=results, query=query)
