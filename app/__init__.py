"""应用工厂：集中配置、CSRF、身份加载、安全头及预期错误。"""
import hmac
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, g, render_template, request, session
from werkzeug.exceptions import HTTPException

from .db import close_db, get_db, init_db


def create_app(test_config=None):
    """创建应用；test_config 用于隔离测试数据库，配置列表见参数文档。"""
    app = Flask(__name__)
    data = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    data.mkdir(parents=True, exist_ok=True)
    secret_path = data / "secret.key"
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(32), encoding="ascii")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY") or secret_path.read_text(encoding="ascii"),
        DATABASE=str(data / "ledger.sqlite3"), MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)

    @app.get("/health")
    def health():
        """供本机启动器识别版本和账本模式；不返回用户或数据库路径。"""
        folder = Path(app.config["DATABASE"]).parent.name
        return {"app": "lizhi-ledger", "version": "2026.11.16.2",
                "profile": folder if folder in ("demo", "public") else "personal"}

    @app.before_request
    def load_identity():
        session.setdefault("csrf", secrets.token_hex(24))
        g.user = get_db().execute("SELECT * FROM users WHERE id=?", (session.get("uid"),)).fetchone()
        if request.method == "POST" and not hmac.compare_digest(
            session["csrf"], request.form.get("csrf", "")
        ):
            abort(400, description="页面凭证已失效，请刷新后重试。")

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def validation_error(error):
        return render_template("error.html", message=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = "文件超过 5 MB，请分批导入。" if error.code == 413 else error.description
        return render_template("error.html", message=message), error.code

    app.jinja_env.filters["money"] = lambda value: f"{value / 100:,.2f}"
    from .web import bp
    app.register_blueprint(bp)
    with app.app_context():
        init_db()
    return app
