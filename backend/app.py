import os

from flask import Flask, abort, send_from_directory

from config import Config
from extensions import db
from models import User
from services.schema_sync import ensure_runtime_schema
from utils.time_utils import to_project_timezone


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(app.config.get("ARTIFACTS_DIR", ""), exist_ok=True)

    db.init_app(app)
 
    from routes.auth import auth_bp, ensure_mock_admin_user
    from routes.courier import courier_bp
    from routes.ai import ai_bp
    from routes.admin import admin_bp
    from routes.box import box_bp
    from routes.user import user_bp
    from routes.profile import profile_bp
    from routes.bind_phone import bind_bp
    from routes.courier_apply import courier_apply_bp
    from routes.integration import integration_bp
    from routes.platform import platform_bp
    from routes.remind import remind_bp

    app.register_blueprint(remind_bp)
    app.register_blueprint(bind_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(box_bp)
    app.register_blueprint(courier_apply_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(courier_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(integration_bp)

    @app.template_filter("dt_local")
    def dt_local(dt):
        if not dt:
            return "-"
        local_dt = to_project_timezone(dt)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")

    @app.get("/")
    def home():
        return "Welcome to the Smart Delivery System!"

    @app.get(f"{Config.ARTIFACTS_URL_PREFIX}/<path:artifact_path>")
    def artifact_file(artifact_path):
        artifact_root = app.config.get("ARTIFACTS_DIR", "")
        if not artifact_root:
            abort(404)
        return send_from_directory(artifact_root, artifact_path)

    with app.app_context():
        ensure_runtime_schema()
        ensure_mock_admin_user()
        print("数据库运行正常")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=os.getenv("FLASK_HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "5000")), debug=False, use_reloader=False)
