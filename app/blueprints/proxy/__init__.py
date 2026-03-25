from flask import Blueprint

proxy_bp = Blueprint('proxy', __name__, url_prefix='/api/db', template_folder='../../templates')
from app.blueprints.proxy import routes
