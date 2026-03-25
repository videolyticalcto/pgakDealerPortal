"""
Backward-compatible routes so existing JS fetch() calls work unchanged.
The JS files call old URLs like /api/dealer-code, /approve/<id>, etc.
These routes redirect or proxy to the new blueprint endpoints.
"""

from flask import Blueprint, redirect, url_for, request, session, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from app.config import Config

compat_bp = Blueprint('compat', __name__)


# ── Old dealer API routes ────────────────────────────────────────────────

@compat_bp.route('/api/dealer-code', methods=['GET'])
def old_dealer_code():
    from app.blueprints.dealer.routes import api_me_dealer_code
    return api_me_dealer_code()


@compat_bp.route('/api/dealer/customers', methods=['GET'])
def old_dealer_customers():
    from app.blueprints.dealer.routes import api_dealer_customers
    return api_dealer_customers()


@compat_bp.route('/dealer/customers', methods=['GET'])
def old_dealer_customers_list():
    from app.blueprints.dealer.routes import get_dealer_customers
    return get_dealer_customers()


@compat_bp.route('/dealer/devices', methods=['GET'])
def old_dealer_devices():
    from app.blueprints.dealer.routes import api_dealer_devices
    return api_dealer_devices()


# ── Old distributor API routes ───────────────────────────────────────────

@compat_bp.route('/api/distributor-code', methods=['GET'])
def old_distributor_code():
    from app.blueprints.distributor.routes import api_me_distributor_code
    return api_me_distributor_code()


@compat_bp.route('/distributor/dealers', methods=['GET'])
def old_distributor_dealers():
    from app.blueprints.distributor.routes import get_distributor_dealers
    return get_distributor_dealers()


@compat_bp.route('/distributor/devices', methods=['GET'])
def old_distributor_devices():
    from app.blueprints.distributor.routes import api_distributor_devices
    return api_distributor_devices()


# ── Old admin API routes ─────────────────────────────────────────────────

@compat_bp.route('/admin/post-users', methods=['POST'])
def old_admin_post_users():
    from app.blueprints.admin.routes import create_user
    return create_user()


@compat_bp.route('/admin/edit-users/<int:user_id>', methods=['PUT'])
def old_admin_edit_users(user_id):
    from app.blueprints.admin.routes import edit_user
    return edit_user(user_id)


@compat_bp.route('/admin/delete-users/<int:user_id>', methods=['DELETE'])
def old_admin_delete_users(user_id):
    from app.blueprints.admin.routes import delete_user
    return delete_user(user_id)


@compat_bp.route('/approve/<int:user_id>', methods=['POST'])
def old_approve(user_id):
    from app.blueprints.admin.routes import approve_dealer
    return approve_dealer(user_id)


@compat_bp.route('/reject/<int:user_id>', methods=['POST'])
def old_reject(user_id):
    from app.blueprints.admin.routes import reject_dealer
    return reject_dealer(user_id)


# ── Old dashboard redirects ──────────────────────────────────────────────

@compat_bp.route('/admin_dashboard', methods=['GET'])
def old_admin_dashboard():
    return redirect(url_for('admin.dashboard'))


@compat_bp.route('/dealer_dashboard', methods=['GET'])
def old_dealer_dashboard():
    return redirect(url_for('dealer.dashboard'))


@compat_bp.route('/distributor_dashboard', methods=['GET'])
def old_distributor_dashboard():
    return redirect(url_for('distributor.dashboard'))
