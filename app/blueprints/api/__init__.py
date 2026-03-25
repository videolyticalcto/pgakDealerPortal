from flask import Blueprint

api_bp = Blueprint('api', __name__, url_prefix='/api', template_folder='../../templates')
from app.blueprints.api import routes
