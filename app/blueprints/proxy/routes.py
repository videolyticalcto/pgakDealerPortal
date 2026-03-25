"""
Proxy Blueprint routes.

Handles: /api/db/analytics, /api/db/devices
Dashboard -> these server routes -> socket_hub_send_command -> agent (clienttt.py)
"""

import os
import traceback

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import request, jsonify

from app.blueprints.proxy import proxy_bp
from app.config import Config
from app.socket_hub import socket_hub_send_command

PG_SSLMODE = os.getenv("PG_SSLMODE", "prefer")


def _build_dsn() -> str:
    return (
        f"host={Config.DB_CONFIG['host']} port={Config.DB_CONFIG['port']} dbname={Config.DB_CONFIG['dbname']} "
        f"user={Config.DB_CONFIG['user']} password={Config.DB_CONFIG['password']} sslmode={PG_SSLMODE}"
    )


def _get_agent_ip_for_proxy():
    """
    Get active agent IP from device_master + system_information tables.
    Returns (TARGET_IP, agent_id) tuple or raises Exception.
    """
    TARGET_IP = None
    agent_id = None

    with psycopg2.connect(_build_dsn()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT serial_number FROM public.device_master
                WHERE serial_number IS NOT NULL
                ORDER BY updated_at DESC LIMIT 1
            """)
            result = cur.fetchone()
            if not result:
                raise Exception("No device_master record found")

            cur.execute("""
                SELECT ip_address FROM public.system_information
                WHERE serial_number = %s AND ip_address IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """, (result['serial_number'],))
            row = cur.fetchone()
            if not row:
                raise Exception("No system_information IP found for serial")

            ip_data = row['ip_address']
            if isinstance(ip_data, dict):
                for key, val in ip_data.items():
                    if isinstance(val, str) and val.count('.') == 3 and not val.startswith('127.'):
                        TARGET_IP = val
                        agent_id = val
                        break
            elif isinstance(ip_data, str) and not ip_data.startswith('127.'):
                TARGET_IP = ip_data
                agent_id = ip_data

    if not TARGET_IP:
        print(f"[proxy] ip_data={ip_data!r} - no valid non-loopback IP found")
        raise Exception("No valid (non-loopback) agent IP found")

    print(f"Proxy -> agent IP: {TARGET_IP}")
    return TARGET_IP, agent_id


def _proxy_via_socket(agent_id, command, data=None, timeout=30):
    """
    Send a command to the agent via socket and return the response dict.
    Raises Exception on failure.
    """
    resp = socket_hub_send_command(
        agent_id=agent_id,
        command=command,
        data=data or {},
        timeout=timeout
    )
    print(f"Socket response for [{command}]: {resp}")
    return resp


# =============================================================================
# ANALYTICS ROUTES
# =============================================================================

@proxy_bp.route("/analytics", methods=["GET"])
def proxy_get_analytics():
    """Proxy: GET agent /api/db/analytics (optional ?cam_ip=)"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        cam_ip = request.args.get("cam_ip", "")
        resp = _proxy_via_socket(agent_id, "get_analytics", {"cam_ip": cam_ip})

        if resp.get("ok") or resp.get("status") == "ok":
            inner = resp.get("data") or {}
            return jsonify({
                "status": "ok",
                "count": inner.get("count", 0),
                "analytics": inner.get("analytics", [])
            }), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[analytics] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@proxy_bp.route("/analytics/<int:analytic_id>", methods=["GET"])
def proxy_get_analytic_by_id(analytic_id):
    """Proxy: GET agent /api/db/analytics/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "get_analytic_by_id", {"analytic_id": analytic_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "analytic": (resp.get("data") or {}).get("analytic", {})}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[analytics/{analytic_id}] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@proxy_bp.route("/analytics/<int:analytic_id>", methods=["PUT"])
def proxy_update_analytic(analytic_id):
    """Proxy: PUT agent /api/db/analytics/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        body = request.get_json(silent=True) or {}
        resp = _proxy_via_socket(agent_id, "update_analytic", {"analytic_id": analytic_id, **body})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Analytic {analytic_id} updated"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[analytics/{analytic_id} PUT] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@proxy_bp.route("/analytics/<int:analytic_id>", methods=["DELETE"])
def proxy_delete_analytic(analytic_id):
    """Proxy: DELETE agent /api/db/analytics/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "delete_analytic", {"analytic_id": analytic_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Analytic {analytic_id} deleted"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[analytics/{analytic_id} DELETE] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# DEVICES ROUTES
# =============================================================================

@proxy_bp.route("/devices", methods=["GET"])
def proxy_get_devices():
    """Proxy: GET agent /api/db/devices (optional ?ip_address= or ?user_id=)"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "get_devices", {
            "ip_address": request.args.get("ip_address", ""),
            "user_id": request.args.get("user_id", "")
        })

        if resp.get("ok") or resp.get("status") == "ok":
            inner = resp.get("data") or {}
            return jsonify({
                "status": "ok",
                "count": inner.get("count", 0),
                "devices": inner.get("devices", [])
            }), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[devices] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@proxy_bp.route("/devices/<int:device_id>", methods=["PUT"])
def proxy_update_device(device_id):
    """Proxy: PUT agent /api/db/devices/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        body = request.get_json(silent=True) or {}
        resp = _proxy_via_socket(agent_id, "update_device", {"device_id": device_id, **body})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Device {device_id} updated"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[devices/{device_id} PUT] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@proxy_bp.route("/devices/<int:device_id>", methods=["DELETE"])
def proxy_delete_device(device_id):
    """Proxy: DELETE agent /api/db/devices/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "delete_device", {"device_id": device_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Device {device_id} deleted"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"[devices/{device_id} DELETE] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
