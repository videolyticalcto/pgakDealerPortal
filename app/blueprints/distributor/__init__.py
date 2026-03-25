from flask import Blueprint

distributor_bp = Blueprint('distributor', __name__, url_prefix='/distributor', template_folder='../../templates')
from app.blueprints.distributor import routes
