import logging
import traceback

import psycopg2
import requests
from psycopg2.extras import RealDictCursor
from flask import jsonify, redirect, render_template, request, session, url_for

from app.blueprints.dealer import dealer_bp
from app.config import Config
from app.utils.db import get_db_conn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions (migrated from main.py, use PRATAP_DB_CONFIG)
# ---------------------------------------------------------------------------

def get_dealer_code(user_id):
    """
    Return dealer_code if the given user is a dealer; otherwise None.
    """
    if not user_id:
        return None

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**Config.PRATAP_DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT dealer_code, usertype FROM users WHERE users_id = %s;",
            (user_id,)
        )
        result = cursor.fetchone()
        if not result:
            return None

        code = result['dealer_code']
        usertype = result['usertype']

        if (usertype or "").lower() != "dealer":
            return None

        return code
    except Exception as e:
        logger.error(f"Error in get_dealer_code: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_customers_by_dealer_code(dealer_code, limit=50, offset=0):
    """
    Fetch users linked to a dealer via referred_by_dealer_code.
    """
    if not dealer_code:
        return []

    # Validate limit and offset to prevent SQL injection and excessive queries
    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        return []

    if limit <= 0 or limit > 500:
        limit = 50

    if offset < 0:
        offset = 0

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**Config.PRATAP_DB_CONFIG)
        cursor = conn.cursor()
        query = """
            SELECT users_id, username, email, phone_no, address, login_count, usertype
            FROM users
            WHERE referred_by_dealer_code = %s
            ORDER BY users_id DESC
            LIMIT %s OFFSET %s;
        """
        cursor.execute(query, (dealer_code, limit, offset))
        return cursor.fetchall() or []
    except Exception as e:
        logger.error(f"Error in get_customers_by_dealer_code: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ===========================================================================
# Page routes
# ===========================================================================

@dealer_bp.route('/dashboard')
def dashboard():
    if 'user_type' not in session or session['user_type'] != 'dealer':
        return redirect(url_for('auth.login'))
    return render_template('dealer/dashboard.html', user_name=session.get("full_name"))


@dealer_bp.route('/customers-page')
def customers_page():
    if 'user_type' not in session or session['user_type'] != 'dealer':
        return redirect(url_for('auth.login'))
    return render_template('dealer/customers.html', user_name=session.get("full_name"))


@dealer_bp.route('/devices-page')
def devices_page():
    if 'user_type' not in session or session['user_type'] != 'dealer':
        return redirect(url_for('auth.login'))
    return render_template('dealer/devices.html', user_name=session.get("full_name"))


@dealer_bp.route('/discovery-page')
def discovery_page():
    if 'user_type' not in session or session['user_type'] != 'dealer':
        return redirect(url_for('auth.login'))
    return render_template('dealer/discovery.html', user_name=session.get("full_name"))


@dealer_bp.route('/analytics-page')
def analytics_page():
    if 'user_type' not in session or session['user_type'] != 'dealer':
        return redirect(url_for('auth.login'))
    return render_template('dealer/analytics.html', user_name=session.get("full_name"))


# ===========================================================================
# API routes
# ===========================================================================

@dealer_bp.route('/api/customers', methods=['GET'])
def api_dealer_customers():
    """
    Proxy endpoint - fetches fresh token when needed
    """
    try:
        print("\n" + "=" * 80)
        print("/dealer/api/customers called")
        print(f"   Session user_type: {session.get('user_type')}")
        print(f"   Session user_id: {session.get('user_id')}")
        print("=" * 80 + "\n")

        # Check authentication
        if 'user_type' not in session or session['user_type'] != 'dealer':
            return jsonify({
                "status": "error",
                "message": "Unauthorized",
                "error_code": "UNAUTHORIZED"
            }), 401

        # Function to get fresh access token
        def get_fresh_access_token():
            dealer_email = session.get('dealer_email')
            dealer_password = session.get('dealer_password')
            dealer_code = session.get('dealer_code')

            if not all([dealer_email, dealer_password, dealer_code]):
                print("Missing credentials in session")
                return None

            print(f"Fetching fresh access token for {dealer_email}...")

            try:
                login_url = "https://api.pgak.co.in/auth/login"
                login_payload = {
                    "email": dealer_email,
                    "password": dealer_password,
                    "dealer_code": dealer_code
                }

                login_response = requests.post(
                    login_url,
                    json=login_payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )

                print(f"Login response: {login_response.status_code}")

                if login_response.status_code == 200:
                    login_data = login_response.json()
                    access_token = login_data.get('access_token')

                    if access_token:
                        print(f"Fresh token: {access_token[:50]}...")
                        session['external_access_token'] = access_token
                        session.modified = True
                        return access_token
                else:
                    print(f"Login failed: {login_response.text[:200]}")

            except Exception as e:
                print(f"Token fetch error: {e}")
                traceback.print_exc()

            return None

        # Try with existing token first
        access_token = session.get('external_access_token')

        if not access_token:
            print("No token in session, getting fresh token...")
            access_token = get_fresh_access_token()

            if not access_token:
                return jsonify({
                    "status": "error",
                    "message": "Failed to authenticate",
                    "error_code": "AUTH_FAILED"
                }), 401

        print(f"Using token: {access_token[:50]}...")

        # Fetch customers
        customers_url = "https://api.pgak.co.in/auth/dealer/customers"

        print(f"Calling external API: {customers_url}")

        response = requests.get(
            customers_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            },
            timeout=30
        )

        print(f"Response: {response.status_code}")

        # If token expired, get fresh token and retry
        if response.status_code == 401:
            print("Token expired, getting fresh token...")

            access_token = get_fresh_access_token()

            if not access_token:
                return jsonify({
                    "status": "error",
                    "message": "Session expired. Please login again.",
                    "error_code": "TOKEN_EXPIRED"
                }), 401

            # Retry with fresh token
            print("Retrying with fresh token...")
            response = requests.get(
                customers_url,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )

            print(f"Retry response: {response.status_code}")

        if response.status_code != 200:
            print(f"API error: {response.text[:500]}")
            return jsonify({
                "status": "error",
                "message": f"API error: {response.status_code}",
                "error_code": "EXTERNAL_API_ERROR"
            }), response.status_code

        data = response.json()
        print(f"Success! Customers: {data.get('count', 0)}")

        return jsonify({
            "status": "success",
            "customers": data.get('customers', []),
            "count": data.get('count', 0)
        }), 200

    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Server error",
            "error_code": "INTERNAL_ERROR"
        }), 500


@dealer_bp.get('/api/code')
def api_me_dealer_code():
    """
    Returns dealer_code for the currently logged-in user.
    """
    user_id = session.get("user_id")
    user_type = session.get("user_type")

    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    # Allow dealer login (and admin if you want)
    if user_type not in ("dealer", "admin", "distributor"):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    # Fast path: if already in session
    session_dealer_code = session.get("dealer_code")
    if session_dealer_code:
        return jsonify({
            "status": "success",
            "user_id": user_id,
            "user_type": user_type,
            "dealer_code": session_dealer_code
        })

    conn = None
    try:
        conn = get_db_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT dealer_code
                FROM public.user_signups
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,)
            )
            row = cur.fetchone()

        dealer_code = (row or {}).get("dealer_code")
        if not dealer_code:
            return jsonify({
                "status": "error",
                "message": "Dealer code not found for this user"
            }), 404

        # Cache in session
        session["dealer_code"] = dealer_code

        return jsonify({
            "status": "success",
            "user_id": user_id,
            "user_type": user_type,
            "dealer_code": dealer_code
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()


@dealer_bp.route('/api/customers-list', methods=['GET'])
def get_dealer_customers():
    """
    Fetch customers linked to a dealer.
    Query params: dealer_id (required), limit (optional, default 50), page (optional, default 1)
    """
    try:
        # Validate and convert dealer_id
        dealer_id = request.args.get('dealer_id')
        if not dealer_id:
            return jsonify({"message": "Dealer ID is required"}), 400

        try:
            dealer_id = int(dealer_id)
        except (TypeError, ValueError):
            return jsonify({"message": "Invalid dealer ID format"}), 400

        # Verify user is actually a dealer
        dealer_code = get_dealer_code(dealer_id)
        if not dealer_code:
            return jsonify({"message": "Access denied: user is not a dealer"}), 403

        # Validate pagination parameters safely
        try:
            limit = int(request.args.get("limit", 50))
            if limit <= 0:
                limit = 50
            if limit > 500:
                limit = 500
        except (TypeError, ValueError):
            limit = 50

        try:
            page = int(request.args.get("page", 1))
            if page <= 0:
                page = 1
        except (TypeError, ValueError):
            page = 1

        offset = (page - 1) * limit

        # Fetch data with error handling
        rows = get_customers_by_dealer_code(dealer_code, limit=limit, offset=offset)

        # Build response with safer data extraction
        customers = []
        for row in rows:
            if row and len(row) >= 7:  # Ensure we have all expected columns
                customers.append({
                    "user_id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "phone_no": row[3],
                    "address": row[4],
                    "login_count": row[5],
                    "usertype": row[6]
                })

        return jsonify({
            "dealer_code": dealer_code,
            "count": len(customers),
            "page": page,
            "limit": limit,
            "customers": customers
        }), 200

    except Exception as exc:
        logger.error(f"Error fetching dealer customers: {exc}", exc_info=True)
        return jsonify({
            "message": "Failed to fetch dealer customers",
            "error": "Internal server error"
        }), 500


@dealer_bp.get('/api/devices-list')
def api_dealer_devices():
    """
    Returns devices issued to a dealer from device_master table.

    Query params:
      dealer_id  = dealer's user_signups.id  (required)
      filter     = online | all              (default: all)
    """
    dealer_id = (request.args.get("dealer_id") or "").strip()
    if not dealer_id:
        return jsonify({"status": "error", "message": "dealer_id is required"}), 400

    try:
        dealer_id_int = int(dealer_id)
    except ValueError:
        return jsonify({"status": "error", "message": "dealer_id must be an integer"}), 400

    filter_type = (request.args.get("filter", "all") or "all").strip().lower()
    if filter_type not in ("all", "online"):
        filter_type = "all"

    def _dt_str(v):
        try:
            from datetime import datetime
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        return v

    sql = """
        WITH dealer_devices AS (
            SELECT
                dm.device_id,
                dm.serial_number,
                dm.dealer_id,
                dm.distributor_id,
                dm.customer_id,
                dm.created_at  AS issued_at,
                dm.updated_at
            FROM public.device_master dm
            WHERE dm.dealer_id = %s
        ),
        latest_status AS (
            SELECT DISTINCT ON (ds.ip_address)
                ds.ip_address,
                ds.hostname,
                ds.status,
                ds.online_at,
                ds.offline_at,
                ds.last_change_at
            FROM device_status ds
            ORDER BY ds.ip_address, ds.last_change_at DESC NULLS LAST, ds.id DESC
        )
        SELECT
            dd.device_id,
            dd.serial_number,
            dd.dealer_id,
            dd.distributor_id,
            dd.customer_id,
            dd.issued_at,

            ls.ip_address,
            ls.hostname,
            ls.status,
            CASE
                WHEN ls.status = 'ONLINE' THEN ls.online_at
                WHEN ls.status = 'OFFLINE' THEN ls.offline_at
                ELSE ls.last_change_at
            END AS last_seen,
            ls.last_change_at,

            si.os,
            si.os_version,
            si.kernel_version,
            si.make,
            si.model,
            si.processor,
            si.machine_type,
            si.mac_addresses,
            si.ip_address AS system_ip_address

        FROM dealer_devices dd
        LEFT JOIN public.system_information si
            ON si.serial_number = dd.serial_number
        LEFT JOIN latest_status ls
            ON ls.hostname = si.hostname

        WHERE (%s = 'all' OR ls.status = 'ONLINE')

        ORDER BY dd.issued_at DESC NULLS LAST
    """

    try:
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (dealer_id_int, filter_type))
                rows = cur.fetchall() or []

        out = []
        for r in rows:
            info = {}
            if r.get('os'):
                info['OS'] = r.get('os')
            if r.get('os_version'):
                info['OS Version'] = r.get('os_version')
            if r.get('kernel_version'):
                info['Kernel Version'] = r.get('kernel_version')
            if r.get('processor'):
                info['Processor'] = r.get('processor')
            machine = r.get('machine_type')
            if machine:
                info['Machine (OS Type)'] = machine
                info['Machine'] = machine
            if r.get('make'):
                info['Make'] = r.get('make')
            if r.get('model'):
                info['Model'] = r.get('model')
            if r.get('serial_number'):
                info['Serial Number'] = r.get('serial_number')
            macs = r.get('mac_addresses')
            if macs is not None:
                info['MAC Addresses'] = macs
            sys_ip = r.get('system_ip_address')
            if isinstance(sys_ip, dict):
                ip_obj = dict(sys_ip)
            elif sys_ip:
                ip_obj = {'Primary': str(sys_ip)}
            else:
                ip_obj = {}
            if r.get('ip_address') and 'Primary' not in ip_obj:
                ip_obj['Primary'] = str(r.get('ip_address'))
            info['IP Address'] = ip_obj

            device = {
                'device_id': r.get('device_id'),
                'serial_number': r.get('serial_number'),
                'dealer_id': r.get('dealer_id'),
                'distributor_id': r.get('distributor_id'),
                'customer_id': r.get('customer_id'),
                'issued_at': _dt_str(r.get('issued_at')),
                'ip_address': r.get('ip_address'),
                'hostname': r.get('hostname'),
                'status': r.get('status') or 'UNKNOWN',
                'last_seen': _dt_str(r.get('last_seen')),
                'last_change_at': _dt_str(r.get('last_change_at')),
                'info': info
            }
            out.append(device)

        return jsonify({
            'status': 'success',
            'dealer_id': dealer_id_int,
            'filter': filter_type,
            'count': len(out),
            'devices': out
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


