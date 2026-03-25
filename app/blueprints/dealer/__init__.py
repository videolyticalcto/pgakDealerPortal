from flask import Blueprint

dealer_bp = Blueprint('dealer', __name__, url_prefix='/dealer', template_folder='../../templates')
from app.blueprints.dealer import routes
