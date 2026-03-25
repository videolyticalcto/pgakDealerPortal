import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from flask import Flask
from flask_cors import CORS
from app.config import Config


def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    app.secret_key = Config.SECRET_KEY
    app.permanent_session_lifetime = Config.PERMANENT_SESSION_LIFETIME

    CORS(app, supports_credentials=True)

    # Register blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.dealer import dealer_bp
    from app.blueprints.distributor import distributor_bp
    from app.blueprints.api import api_bp
    from app.blueprints.devices import devices_bp
    from app.blueprints.proxy import proxy_bp
    from app.blueprints.assets import assets_bp
    from app.blueprints.compat import compat_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dealer_bp)
    app.register_blueprint(distributor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(compat_bp)  # backward-compatible old URLs

    return app
