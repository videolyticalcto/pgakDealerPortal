from flask import Blueprint

devices_bp = Blueprint('devices', __name__, template_folder='../../templates')
from app.blueprints.devices import routes
