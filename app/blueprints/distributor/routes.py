import logging
import traceback

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import jsonify, redirect, render_template, request, session, url_for

from app.blueprints.distributor import distributor_bp
from app.config import Config
from app.utils.db import get_db_conn

logger = logging.getLogger(__name__)


# ===========================================================================
# Page routes
# ===========================================================================

@distributor_bp.route('/dashboard')
def dashboard():
    if 'user_type' not in session or session['user_type'] != 'distributor':
        return redirect(url_for('auth.login'))
    return render_template('distributor/dashboard.html', user_name=session.get("full_name"))


@distributor_bp.route('/dealers-page')
def dealers_page():
    if 'user_type' not in session or session['user_type'] != 'distributor':
        return redirect(url_for('auth.login'))
    return render_template('distributor/dealers.html', user_name=session.get("full_name"))


@distributor_bp.route('/devices-page')
def devices_page():
    if 'user_type' not in session or session['user_type'] != 'distributor':
        return redirect(url_for('auth.login'))
    return render_template('distributor/devices.html', user_name=session.get("full_name"))


# ===========================================================================
# API routes
# ===========================================================================

@distributor_bp.route('/api/dealers', methods=['GET'])
def get_distributor_dealers():
    """
    Fetch all dealers under the current logged-in distributor.

    Returns:
        JSON with list of all dealers whose distributor_code matches the distributor's code
    """
    try:
        # Check if user is logged in and is a distributor
        if 'user_type' not in session or session['user_type'] != 'distributor':
            return jsonify({
                "status": "error",
                "message": "Unauthorized - Distributor access only"
            }), 403

        distributor_id = session.get('user_id')

        with get_db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Single JOIN query instead of two sequential queries
                cursor.execute("""
                    SELECT
                        d.user_id,
                        d.full_name,
                        d.address,
                        d.email,
                        d.phone_number,
                        d.gst_no,
                        d.company_name,
                        d.pincode,
                        d.dealer_code,
                        d.status,
                        d.created_at
                    FROM user_signups dist
                    JOIN user_signups d
                        ON d.user_type = 'dealer'
                        AND d.distributor_code = dist.distributor_code
                    WHERE dist.user_id = %s AND dist.user_type = 'distributor'
                    ORDER BY d.created_at DESC
                """, (distributor_id,))

                dealers = cursor.fetchall()

        if dealers is None:
            dealers = []

        # Convert to list format
        dealers_list = []
        for dealer in dealers:
            dealers_list.append({
                "user_id": dealer['user_id'],
                "full_name": dealer['full_name'],
                "address": dealer['address'],
                "email": dealer['email'],
                "phone_number": dealer['phone_number'],
                "gst_no": dealer['gst_no'],
                "company_name": dealer['company_name'],
                "pincode": dealer['pincode'],
                "dealer_code": dealer['dealer_code'],
                "status": dealer['status'],
                "created_at": dealer['created_at'].isoformat() if dealer['created_at'] else None
            })

        return jsonify({
            "status": "success",
            "distributor_name": distributor['full_name'],
            "distributor_company": distributor['company_name'],
            "distributor_code": distributor_code,
            "total_dealers": len(dealers_list),
            "dealers": dealers_list
        }), 200

    except Exception as e:
        print("---- /distributor/api/dealers ERROR ----")
        traceback.print_exc()
        print("----------------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch dealers",
            "error": str(e)
        }), 500


@distributor_bp.get('/api/code')
def api_me_distributor_code():
    """
    Returns distributor_code for the currently logged-in user.
    Works for distributor login (and optionally admin if needed).
    """
    user_id = session.get("user_id")
    user_type = session.get("user_type")

    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    # Only distributor users (and admin) can access this
    if user_type not in ("distributor", "admin"):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    # Fast path: if you already stored it in session at login, return it directly
    session_dist_code = session.get("distributor_code")
    if session_dist_code:
        return jsonify({
            "status": "success",
            "user_id": user_id,
            "user_type": user_type,
            "distributor_code": session_dist_code
        })

    # Otherwise fetch from DB
    conn = None
    try:
        conn = get_db_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT distributor_code
                FROM public.user_signups
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,)
            )
            row = cur.fetchone()

        distributor_code = (row or {}).get("distributor_code")
        if not distributor_code:
            return jsonify({
                "status": "error",
                "message": "Distributor code not found for this user"
            }), 404

        # Cache in session so next calls don't hit DB
        session["distributor_code"] = distributor_code

        return jsonify({
            "status": "success",
            "user_id": user_id,
            "user_type": user_type,
            "distributor_code": distributor_code
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()


@distributor_bp.get('/api/devices-list')
def api_distributor_devices():
    """
    Returns devices issued to a distributor from device_master table.

    Query params:
      distributor_id  = distributor's user_signups.id  (required)
      filter          = online | all                    (default: all)
    """
    distributor_id = (request.args.get("distributor_id") or "").strip()
    if not distributor_id:
        return jsonify({"status": "error", "message": "distributor_id is required"}), 400

    try:
        distributor_id_int = int(distributor_id)
    except ValueError:
        return jsonify({"status": "error", "message": "distributor_id must be an integer"}), 400

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
        WITH dist_devices AS (
            SELECT
                dm.device_id,
                dm.serial_number,
                dm.dealer_id,
                dm.distributor_id,
                dm.customer_id,
                dm.created_at  AS issued_at,
                dm.updated_at
            FROM public.device_master dm
            WHERE dm.distributor_id = %s
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

        FROM dist_devices dd
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
                cur.execute(sql, (distributor_id_int, filter_type))
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
            'distributor_id': distributor_id_int,
            'filter': filter_type,
            'count': len(out),
            'devices': out
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
