from flask import Blueprint

assets_bp = Blueprint('assets', __name__, url_prefix='/api/assets', template_folder='../../templates')
from app.blueprints.assets import routes
