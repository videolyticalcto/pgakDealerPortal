"""
API Blueprint routes.

Handles: /api/validate-device-serial, /api/devices/save-from-qr,
         /api/devices/save-from-qr-v2, /api/agents, /api/scan,
         /api/scan_results, /api/scan-db
"""

import hashlib
import json
import logging
import threading
import time
import traceback
from datetime import datetime, timezone

import psycopg2
import requests as http_requests
from psycopg2.extras import RealDictCursor
from flask import request, jsonify, Response, send_file
import subprocess
import shutil
from urllib.parse import urlparse, urlunparse, quote

from app.blueprints.api import api_bp
from app.config import Config
from app.socket_hub import socket_hub_send_command, _SOCKET_AGENT_CONNS, _SOCKET_AGENT_LOCK

logger = logging.getLogger(__name__)

# ── In-memory stores for agents (device_discovery verified) ────────────────
import os

AGENTS_LOCK = threading.Lock()
AGENTS = {}

PG_SSLMODE = os.getenv("PG_SSLMODE", "prefer")


def _build_dsn() -> str:
    return (
        f"host={Config.DB_CONFIG['host']} port={Config.DB_CONFIG['port']} dbname={Config.DB_CONFIG['dbname']} "
        f"user={Config.DB_CONFIG['user']} password={Config.DB_CONFIG['password']} sslmode={PG_SSLMODE}"
    )


def _normalize_agent_id(agent_id: str) -> str:
    """
    Normalize agent_id to just IP address (remove port if present)
    Example: "192.168.1.111:5001" -> "192.168.1.111"
    """
    agent_id = (agent_id or "").strip()
    if ":" in agent_id and agent_id.count(".") == 3:
        parts = agent_id.split(":")
        if len(parts) == 2:
            ip_part = parts[0]
            if all(part.isdigit() for part in ip_part.split(".") if part.isdigit()):
                return ip_part
    return agent_id


def _resolve_agent_ip_from_serial(serial_number: str):
    """
    Resolve the current agent IP for a given serial_number from system_information.
    Picks eth*/wlan*/other non-loopback in that order. Returns None if unresolved.
    """
    serial_number = (serial_number or "").strip()
    if not serial_number:
        return None
    try:
        with psycopg2.connect(_build_dsn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ip_address
                    FROM public.system_information
                    WHERE serial_number = %s
                      AND ip_address IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (serial_number,),
                )
                row = cur.fetchone()
        if not row:
            return None
        ip_data = row["ip_address"]

        def _ok(v):
            return (
                isinstance(v, str)
                and v.count(".") == 3
                and not v.startswith("127.")
                and v != "0.0.0.0"
            )

        if isinstance(ip_data, dict):
            for prefix in ("eth", "wlan"):
                for k, v in sorted(ip_data.items()):
                    if k.startswith(prefix) and _ok(v):
                        return v
            for k, v in sorted(ip_data.items()):
                if _ok(v):
                    return v
            return None
        if _ok(ip_data):
            return ip_data
        return None
    except Exception as e:
        logger.warning(f"_resolve_agent_ip_from_serial error for {serial_number}: {e}")
        return None


# ── Scan results storage ───────────────────────────────────────────────────
SCAN_RESULTS_LOCK = threading.Lock()
SCAN_RESULTS = {}  # scan_key -> {"count": N, "devices": [...], "timestamp": T}


def _generate_scan_key(agent_id: str, username: str, password: str) -> str:
    """Generate unique key for scan request to match with async results."""
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()[:16]
    return f"{agent_id}:{username}:{pwd_hash}"


def _wait_for_scan_results(agent_id: str, username: str, password: str,
                           max_wait_seconds: int = 40, poll_interval: int = 2) -> dict:
    """
    Poll for scan results that will be posted by agent via callback endpoint.
    Returns dict with 'count' and 'devices' keys, or None if timeout.
    """
    scan_key = _generate_scan_key(agent_id, username, password)
    start_time = time.time()
    attempts = 0

    print(f"   Polling for scan results with key: {scan_key}")

    while (time.time() - start_time) < max_wait_seconds:
        attempts += 1

        with SCAN_RESULTS_LOCK:
            if scan_key in SCAN_RESULTS:
                results = SCAN_RESULTS.pop(scan_key)
                elapsed = time.time() - start_time
                print(f"   Scan results received after {elapsed:.1f}s (attempt #{attempts})")
                return results

        if attempts % 5 == 0:
            elapsed = time.time() - start_time
            print(f"   Still waiting for scan results... {elapsed:.0f}s elapsed")

        time.sleep(poll_interval)

    elapsed = time.time() - start_time
    print(f"   Scan results polling timeout after {elapsed:.1f}s ({attempts} attempts)")
    return None


def _cleanup_old_scan_results():
    """Remove scan results older than 10 minutes."""
    while True:
        try:
            time.sleep(60)
            with SCAN_RESULTS_LOCK:
                now = time.time()
                expired_keys = [
                    key for key, value in SCAN_RESULTS.items()
                    if (now - value.get("timestamp", 0)) > 600
                ]
                for key in expired_keys:
                    del SCAN_RESULTS[key]
                if expired_keys:
                    print(f"Cleaned up {len(expired_keys)} expired scan results")
        except Exception as e:
            print(f"Error in scan results cleanup: {e}")


# Start cleanup thread
threading.Thread(target=_cleanup_old_scan_results, daemon=True).start()


# =============================================================================
# ROUTES
# =============================================================================

@api_bp.route('/validate-device-serial', methods=['POST'])
def validate_device_serial():
    """
    Validates if a scanned serial number exists in the system_information table and fetches the IP address.
    """
    conn = None
    cur = None

    try:
        if not request.is_json:
            return jsonify({
                "success": False,
                "message": "Content-Type must be application/json",
                "error_code": "INVALID_CONTENT_TYPE"
            }), 400

        data = request.get_json(silent=True) or {}

        if not data.get("serial_number"):
            return jsonify({
                "success": False,
                "message": "serial_number is required",
                "error_code": "MISSING_SERIAL_NUMBER"
            }), 400

        serial_number = str(data.get("serial_number", "")).strip()

        if len(serial_number) < 3:
            return jsonify({
                "success": False,
                "message": "serial_number must be at least 3 characters",
                "error_code": "INVALID_SERIAL_NUMBER"
            }), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cur = conn.cursor()

        check_sql = """
            SELECT
                device_id,
                serial_number,
                ip_address,
                make,
                model,
                created_at
            FROM public.system_information
            WHERE serial_number = %s
            LIMIT 1;
        """

        cur.execute(check_sql, (serial_number,))
        result = cur.fetchone()

        if result is None:
            logger.warning(f"Serial number validation failed: {serial_number} not found in system_information")
            return jsonify({
                "success": False,
                "message": f'Serial number "{serial_number}" not found in system information',
                "error_code": "SERIAL_NOT_FOUND",
                "serial_number": serial_number
            }), 404

        created_at = result[5]
        created_date = None
        created_time = None

        if created_at:
            created_date = created_at.strftime('%Y-%m-%d')
            created_time = created_at.strftime('%H:%M:%S')

        device_info = {
            "id": result[0],
            "serial_number": result[1],
            "ip_address": result[2],
            "make": result[3],
            "model": result[4],
            "created_date": created_date,
            "created_time": created_time
        }

        logger.info(f"Serial number validation successful: {serial_number} found in system_information")

        return jsonify({
            "success": True,
            "message": "Serial number found in system information",
            "error_code": None,
            "serial_number": serial_number,
            "device_info": device_info
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error validating serial number: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Validation failed: {str(e)}",
            "error_code": "VALIDATION_ERROR"
        }), 500

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@api_bp.route('/devices/save-from-qr', methods=['POST'])
def save_device_qr():
    conn = None
    cur = None

    try:
        if not request.is_json:
            return jsonify({
                "success": False,
                "message": "Content-Type must be application/json",
                "error_code": "INVALID_CONTENT_TYPE"
            }), 400

        data = request.get_json(silent=True) or {}

        required_fields = ["serial_number", "user_id", "user_type"]
        missing_fields = [f for f in required_fields if not data.get(f)]

        if missing_fields:
            return jsonify({
                "success": False,
                "message": f"Missing required fields: {', '.join(missing_fields)}",
                "error_code": "MISSING_REQUIRED_FIELDS"
            }), 400

        serial_number = str(data.get("serial_number", "")).strip()
        user_id = int(data.get("user_id"))
        user_type = str(data.get("user_type", "")).strip().lower()
        qr_data = str(data.get("qr_data", "")).strip()
        is_admin = bool(data.get("is_admin", False))

        if len(serial_number) < 3:
            return jsonify({
                "success": False,
                "message": "serial_number must be at least 3 characters",
                "error_code": "INVALID_SERIAL_NUMBER"
            }), 400

        valid_user_types = ["dealer", "distributor", "customer"]
        if user_type not in valid_user_types:
            return jsonify({
                "success": False,
                "message": f"user_type must be one of: {', '.join(valid_user_types)}",
                "error_code": "INVALID_USER_TYPE"
            }), 400

        now = datetime.now(timezone.utc)
        conn = psycopg2.connect(**Config.DB_CONFIG)
        cur = conn.cursor()

        parent_distributor_id = None
        parent_dealer_id = None
        user_distributor_code = None
        user_dealer_code = None

        if user_type == "distributor":
            get_distributor_sql = """
                SELECT user_id, distributor_code
                FROM public.user_signups
                WHERE user_id = %s AND user_type = 'distributor'
                LIMIT 1;
            """
            cur.execute(get_distributor_sql, (user_id,))
            dist_record = cur.fetchone()

            if dist_record is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Distributor ID {user_id} does not exist or is not registered as distributor',
                    "error_code": "INVALID_DISTRIBUTOR_ID"
                }), 400

            parent_distributor_id = dist_record[0]
            user_distributor_code = dist_record[1]
            logger.info(f"User is DISTRIBUTOR - ID: {user_id}, Code: {user_distributor_code}")

        elif user_type == "dealer":
            get_dealer_info_sql = """
                SELECT user_id, dealer_code, distributor_code
                FROM public.user_signups
                WHERE user_id = %s AND user_type = 'dealer'
                LIMIT 1;
            """
            cur.execute(get_dealer_info_sql, (user_id,))
            dealer_record = cur.fetchone()

            if dealer_record is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Dealer ID {user_id} does not exist or is not registered as dealer',
                    "error_code": "INVALID_DEALER_ID"
                }), 400

            dealer_user_id = dealer_record[0]
            user_dealer_code = dealer_record[1]
            user_distributor_code = dealer_record[2]

            if user_distributor_code is not None:
                get_distributor_by_code_sql = """
                    SELECT user_id
                    FROM public.user_signups
                    WHERE distributor_code = %s AND user_type = 'distributor'
                    LIMIT 1;
                """
                cur.execute(get_distributor_by_code_sql, (user_distributor_code,))
                dist_by_code = cur.fetchone()

                if dist_by_code is not None:
                    parent_distributor_id = dist_by_code[0]
                    logger.info(
                        f"Dealer ID {user_id} (Code: {user_dealer_code}) is under Distributor ID {parent_distributor_id}"
                    )
                else:
                    logger.warning(
                        f"Dealer ID {user_id} has invalid distributor code: {user_distributor_code}"
                    )
            else:
                logger.info(
                    f"Dealer ID {user_id} (Code: {user_dealer_code}) has NO parent distributor - Independent dealer"
                )

        elif user_type == "customer":
            get_customer_info_sql = """
                SELECT user_id, distributor_code, dealer_code
                FROM public.user_signups
                WHERE user_id = %s AND user_type = 'customer'
                LIMIT 1;
            """
            cur.execute(get_customer_info_sql, (user_id,))
            customer_record = cur.fetchone()

            if customer_record is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Customer ID {user_id} does not exist or is not registered as customer',
                    "error_code": "INVALID_CUSTOMER_ID"
                }), 400

            customer_user_id = customer_record[0]
            customer_distributor_code = customer_record[1]
            customer_dealer_code = customer_record[2]

            if customer_distributor_code is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Customer ID {user_id} is not assigned to any distributor. Customer must have parent distributor.',
                    "error_code": "CUSTOMER_NO_DISTRIBUTOR"
                }), 400

            get_distributor_by_code_sql = """
                SELECT user_id
                FROM public.user_signups
                WHERE distributor_code = %s AND user_type = 'distributor'
                LIMIT 1;
            """
            cur.execute(get_distributor_by_code_sql, (customer_distributor_code,))
            dist_by_code = cur.fetchone()

            if dist_by_code is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Customer ID {user_id} has invalid distributor assignment',
                    "error_code": "CUSTOMER_INVALID_DISTRIBUTOR"
                }), 400

            parent_distributor_id = dist_by_code[0]

            if customer_dealer_code is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Customer ID {user_id} must have parent dealer assigned.',
                    "error_code": "CUSTOMER_NO_DEALER"
                }), 400

            get_dealer_by_code_sql = """
                SELECT user_id
                FROM public.user_signups
                WHERE dealer_code = %s AND user_type = 'dealer'
                LIMIT 1;
            """
            cur.execute(get_dealer_by_code_sql, (customer_dealer_code,))
            dealer_by_code = cur.fetchone()

            if dealer_by_code is None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": f'Customer ID {user_id} has invalid dealer assignment',
                    "error_code": "CUSTOMER_INVALID_DEALER"
                }), 400

            parent_dealer_id = dealer_by_code[0]

            logger.info(
                f"Customer ID {user_id} is under Distributor ID {parent_distributor_id} and Dealer ID {parent_dealer_id}"
            )

        # STEP 2: Check if serial exists in system_information
        check_sys_info_sql = """
            SELECT device_id
            FROM public.system_information
            WHERE serial_number = %s
            LIMIT 1;
        """
        cur.execute(check_sys_info_sql, (serial_number,))
        sys_info = cur.fetchone()

        if sys_info is None:
            conn.rollback()
            return jsonify({
                "success": False,
                "message": f'Serial number "{serial_number}" does not exist in system information',
                "error_code": "SERIAL_NOT_IN_SYSTEM_INFO"
            }), 400

        # STEP 3: Check if device already exists in device_master
        check_device_sql = """
            SELECT device_id, dealer_id, distributor_id, customer_id
            FROM public.device_master
            WHERE serial_number = %s
            LIMIT 1;
        """
        cur.execute(check_device_sql, (serial_number,))
        existing_device = cur.fetchone()

        device_id = None
        is_new_device = existing_device is None

        if existing_device:
            device_id, assigned_dealer_id, assigned_distributor_id, assigned_customer_id = existing_device

            # DISTRIBUTOR ASSIGNMENT LOGIC
            if user_type == "distributor":
                if assigned_distributor_id is not None and assigned_distributor_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Distributor ID {assigned_distributor_id}. Cannot reassign.',
                        "error_code": "DEVICE_ASSIGNED_TO_DIFFERENT_DISTRIBUTOR",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                if assigned_dealer_id is not None and not is_admin:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Dealer ID {assigned_dealer_id}. Cannot reassign to Distributor.',
                        "error_code": "DEVICE_HAS_DEALER_ASSIGNMENT",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

            # DEALER ASSIGNMENT LOGIC
            elif user_type == "dealer":
                if assigned_dealer_id is not None and assigned_dealer_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Dealer ID {assigned_dealer_id}. Cannot reassign to Dealer ID {user_id}.',
                        "error_code": "DEVICE_ALREADY_ASSIGNED_DEALER",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                if assigned_customer_id is not None:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Customer ID {assigned_customer_id}. Dealer cannot reassign.',
                        "error_code": "DEVICE_HAS_CUSTOMER_ASSIGNMENT",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                if parent_distributor_id is None:
                    if assigned_distributor_id is not None:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": f'Dealer ID {user_id} has no parent distributor. Cannot claim device already assigned to Distributor ID {assigned_distributor_id}.',
                            "error_code": "DEALER_NO_PARENT_DISTRIBUTOR",
                            "serial_number": serial_number,
                            "device_id": device_id,
                            "device_has_distributor": assigned_distributor_id
                        }), 409
                else:
                    if assigned_distributor_id is not None and assigned_distributor_id != parent_distributor_id:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": f'Device belongs to Distributor ID {assigned_distributor_id}. You (Dealer ID {user_id}) belong to Distributor ID {parent_distributor_id}. Mismatch.',
                            "error_code": "DISTRIBUTOR_MISMATCH",
                            "serial_number": serial_number,
                            "device_id": device_id
                        }), 409

                logger.info(f"Device {serial_number} validation passed for Dealer claim.")

            # CUSTOMER ASSIGNMENT LOGIC
            elif user_type == "customer":
                if assigned_customer_id is not None and assigned_customer_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Customer ID {assigned_customer_id}. Cannot reassign.',
                        "error_code": "DEVICE_ALREADY_ASSIGNED_CUSTOMER",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                if assigned_distributor_id is not None and assigned_distributor_id != parent_distributor_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device belongs to Distributor ID {assigned_distributor_id}. You (Customer) belong to Distributor ID {parent_distributor_id}. Mismatch.',
                        "error_code": "DISTRIBUTOR_MISMATCH",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                if assigned_dealer_id is not None and assigned_dealer_id != parent_dealer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device belongs to Dealer ID {assigned_dealer_id}. You (Customer) belong to Dealer ID {parent_dealer_id}. Mismatch.',
                        "error_code": "DEALER_MISMATCH",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                logger.info(f"Device {serial_number} validation passed for Customer claim.")

        # STEP 4: INSERT vs UPDATE LOGIC
        if is_new_device:
            if user_type == "distributor":
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (serial_number, None, parent_distributor_id, None, now, now))
                row = cur.fetchone()
                device_id = row[0]
                logger.info(
                    f"Device INSERTED by DISTRIBUTOR - ID: {device_id}, Serial: {serial_number}, Dist: {parent_distributor_id}"
                )

            elif user_type == "dealer":
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (serial_number, user_id, parent_distributor_id, None, now, now))
                row = cur.fetchone()
                device_id = row[0]

                if parent_distributor_id:
                    logger.info(
                        f"Device INSERTED by DEALER - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: {parent_distributor_id}"
                    )
                else:
                    logger.info(
                        f"Device INSERTED by DEALER (Independent) - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: NULL"
                    )

            elif user_type == "customer":
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (serial_number, parent_dealer_id, parent_distributor_id, user_id, now, now))
                row = cur.fetchone()
                device_id = row[0]
                logger.info(
                    f"Device INSERTED by CUSTOMER - ID: {device_id}, Serial: {serial_number}, Customer: {user_id}, Dealer: {parent_dealer_id}, Dist: {parent_distributor_id}"
                )

        else:
            if user_type == "distributor":
                update_sql = """
                    UPDATE public.device_master
                    SET distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (parent_distributor_id, now, device_id))
                logger.info(
                    f"Device UPDATED by DISTRIBUTOR - ID: {device_id}, Serial: {serial_number}, Dist: {parent_distributor_id}"
                )

            elif user_type == "dealer":
                update_sql = """
                    UPDATE public.device_master
                    SET dealer_id = %s, distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (user_id, parent_distributor_id, now, device_id))
                logger.info(
                    f"Device UPDATED by DEALER - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: {parent_distributor_id}"
                )

            elif user_type == "customer":
                update_sql = """
                    UPDATE public.device_master
                    SET customer_id = %s, dealer_id = %s, distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (user_id, parent_dealer_id, parent_distributor_id, now, device_id))
                logger.info(
                    f"Device UPDATED by CUSTOMER - ID: {device_id}, Serial: {serial_number}, Customer: {user_id}, Dealer: {parent_dealer_id}, Dist: {parent_distributor_id}"
                )

        conn.commit()

        user_type_label = "DEALER" if user_type == "dealer" else "DISTRIBUTOR" if user_type == "distributor" else "CUSTOMER"
        action = "INSERTED" if is_new_device else "UPDATED"

        return jsonify({
            "success": True,
            "message": f"Device ISSUED TO {user_type_label} ({action}) SUCCESSFULLY",
            "device_id": device_id,
            "user_id": user_id,
            "user_type": user_type,
            "serial_number": serial_number,
            "qr_data": qr_data,
            "action": action,
            "parent_distributor_id": parent_distributor_id,
            "parent_dealer_id": parent_dealer_id,
            "timestamp": now.isoformat()
        }), 201

    except ValueError as ve:
        if conn:
            conn.rollback()
        logger.error(f"ValueError in save_device_qr: {str(ve)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Invalid data format: {str(ve)}",
            "error_code": "INVALID_DATA_FORMAT"
        }), 400

    except psycopg2.Error as db_err:
        if conn:
            conn.rollback()
        logger.error(f"Database error in save_device_qr: {str(db_err)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Database operation failed",
            "error_code": "DATABASE_ERROR"
        }), 500

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Unexpected error in save_device_qr: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Failed to save device: {str(e)}",
            "error_code": "INTERNAL_SERVER_ERROR"
        }), 500

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@api_bp.route('/devices/save-from-qr-v2', methods=['POST'])
def save_device_qr_v2():
    conn = None
    cur = None

    try:
        if not request.is_json:
            return jsonify({"success": False, "message": "Invalid content type"}), 400

        data = request.get_json(silent=True) or {}

        if not data.get("serial_number") or not data.get("user_id") or not data.get("user_type"):
            return jsonify({"success": False, "message": "Missing required fields"}), 400

        serial_number = str(data.get("serial_number", "")).strip()
        user_id = int(data.get("user_id"))
        user_type = str(data.get("user_type", "")).strip().lower()
        customer_id = data.get("customer_id")

        if customer_id:
            customer_id = int(customer_id)

        if len(serial_number) < 3:
            return jsonify({"success": False, "message": "Invalid serial number"}), 400

        if user_type not in ["dealer", "distributor", "customer"]:
            return jsonify({"success": False, "message": "Invalid user type"}), 400

        now = datetime.now(timezone.utc)
        conn = psycopg2.connect(**Config.DB_CONFIG)
        cur = conn.cursor()

        logger.info(f"{user_type.upper()} {user_id} scanning: {serial_number}")

        parent_distributor_id = None
        parent_dealer_id = None

        if user_type == "distributor":
            cur.execute("SELECT user_id FROM public.user_signups WHERE user_id = %s AND user_type = 'distributor' LIMIT 1;", (user_id,))
            if cur.fetchone() is None:
                conn.rollback()
                return jsonify({"success": False, "message": f"Distributor {user_id} not found"}), 400
            parent_distributor_id = user_id

        elif user_type == "dealer":
            cur.execute("SELECT user_id, distributor_code FROM public.user_signups WHERE user_id = %s AND user_type = 'dealer' LIMIT 1;", (user_id,))
            dealer_record = cur.fetchone()
            if dealer_record is None:
                conn.rollback()
                return jsonify({"success": False, "message": f"Dealer {user_id} not found"}), 400

            parent_dealer_id = dealer_record[0]
            dealer_distributor_code = dealer_record[1]

            if dealer_distributor_code:
                cur.execute("SELECT user_id FROM public.user_signups WHERE distributor_code = %s AND user_type = 'distributor' LIMIT 1;", (dealer_distributor_code,))
                dist_record = cur.fetchone()
                if dist_record:
                    parent_distributor_id = dist_record[0]

        elif user_type == "customer":
            if customer_id is None:
                customer_id = user_id

        # Check serial exists in system_information
        cur.execute("SELECT device_id FROM public.system_information WHERE serial_number = %s LIMIT 1;", (serial_number,))
        if cur.fetchone() is None:
            conn.rollback()
            return jsonify({"success": False, "message": "Serial not found"}), 400

        # Check device status in device_master
        cur.execute("SELECT device_id, dealer_id, distributor_id, customer_id FROM public.device_master WHERE serial_number = %s LIMIT 1;", (serial_number,))
        existing_device = cur.fetchone()

        device_id = None
        is_new_device = existing_device is None

        # CUSTOMER FLOW
        if user_type == "customer":
            if is_new_device:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "Device not issued by Admin. Contact your Dealer/Distributor first."
                }), 403

            device_id, assigned_dealer_id, assigned_distributor_id, assigned_customer_id = existing_device

            if not assigned_dealer_id and not assigned_distributor_id:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "Device not assigned to any Dealer/Distributor. Contact Admin."
                }), 403

            if assigned_customer_id:
                if assigned_customer_id == customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "Device already registered to you."
                    }), 409
                else:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "Device already assigned to another Customer."
                    }), 409

            cur.execute(
                "UPDATE public.device_master SET customer_id = %s, updated_at = %s WHERE device_id = %s;",
                (customer_id, now, device_id)
            )
            action = "UPDATED"

        # DISTRIBUTOR FLOW
        elif user_type == "distributor":
            if is_new_device:
                cur.execute("""
                    INSERT INTO public.device_master
                    (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """, (serial_number, None, parent_distributor_id, None, now, now))
                device_id = cur.fetchone()[0]
                action = "INSERTED"
            else:
                device_id, assigned_dealer_id, assigned_distributor_id, assigned_customer_id = existing_device

                if assigned_distributor_id:
                    if assigned_distributor_id == parent_distributor_id:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Device already issued to you."
                        }), 409
                    else:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Device already assigned to another Distributor."
                        }), 409

                if assigned_dealer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "Device already assigned to a Dealer."
                    }), 409

                if assigned_customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "Device already assigned to a Customer."
                    }), 409

                cur.execute(
                    "UPDATE public.device_master SET distributor_id = %s, updated_at = %s WHERE device_id = %s;",
                    (parent_distributor_id, now, device_id)
                )
                action = "UPDATED"

        # DEALER FLOW
        elif user_type == "dealer":
            if is_new_device:
                cur.execute("""
                    INSERT INTO public.device_master
                    (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """, (serial_number, parent_dealer_id, parent_distributor_id, None, now, now))
                device_id = cur.fetchone()[0]
                action = "INSERTED"
            else:
                device_id, assigned_dealer_id, assigned_distributor_id, assigned_customer_id = existing_device

                if assigned_dealer_id:
                    if assigned_dealer_id == parent_dealer_id:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Device already issued to you."
                        }), 409
                    else:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Device already assigned to another Dealer."
                        }), 409

                if assigned_customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "Device already assigned to a Customer."
                    }), 409

                cur.execute(
                    "UPDATE public.device_master SET dealer_id = %s, distributor_id = %s, updated_at = %s WHERE device_id = %s;",
                    (parent_dealer_id, parent_distributor_id, now, device_id)
                )
                action = "UPDATED"

        conn.commit()

        return jsonify({
            "success": True,
            "message": f"Device {action} successfully",
            "device_id": device_id,
            "serial_number": serial_number,
            "user_id": user_id,
            "user_type": user_type,
            "action": action,
            "distributor_id": parent_distributor_id,
            "dealer_id": parent_dealer_id,
            "customer_id": customer_id if user_type == "customer" else None,
            "timestamp": now.isoformat()
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@api_bp.route("/agents", methods=["GET"])
def api_agents():
    with AGENTS_LOCK:
        agents_list = list(AGENTS.values())
    agents_list.sort(key=lambda x: x.get("last_seen_ts", 0), reverse=True)
    return jsonify({"ok": True, "count": len(agents_list), "agents": agents_list})


@api_bp.route("/scan", methods=["POST"])
def api_scan():
    """
    UI calls this to trigger ONVIF network scan on a connected agent.

    Agent selection priority:
      1. agent_id/agent_ip provided in request body
      2. AGENTS dict (device_discovery verified target, PRIMARY)
      3. Socket hub _SOCKET_AGENT_CONNS (SECONDARY, fallback only)
      4. No agent found -> 503
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    customer_id = data.get("customer_id")

    print(f"\n{'='*70}")
    print(f"SCAN REQUEST")
    print(f"   Username: {username}")
    print(f"   Password: {'*' * len(password)}")
    print(f"   Customer ID: {customer_id}")
    print(f"   Customer ID type: {type(customer_id).__name__}")
    print(f"   Raw request body: {data}")
    print(f"{'='*70}")

    if not username or not password:
        return jsonify({"status": "error", "message": "username/password required"}), 400

    # Step 1: Prefer serial_number-based resolution (most reliable)
    serial_number = (data.get("serial_number") or data.get("serial") or "").strip()
    agent_id = ""
    if serial_number:
        resolved_ip = _resolve_agent_ip_from_serial(serial_number)
        if resolved_ip:
            agent_id = resolved_ip
            print(f"   Resolved agent_id from serial_number '{serial_number}': {agent_id}")
        else:
            print(f"   serial_number '{serial_number}' provided but no IP found in system_information")
            return jsonify({
                "status": "error",
                "message": f"Could not resolve IP for serial_number '{serial_number}'. Device not registered or has no IP.",
                "serial_number": serial_number,
            }), 404

    # Step 1b: Fallback - Get agent_id from request body
    if not agent_id:
        raw_agent_id = (data.get("agent_id") or data.get("agent") or "").strip()
        agent_id = _normalize_agent_id(raw_agent_id) if raw_agent_id else ""
        print(f"   Requested agent_id: '{agent_id}'")

    # Step 2: Auto-select VERIFIED agent from AGENTS dict
    if not agent_id:
        print("   No agent_id in request - selecting verified target from AGENTS...")

        with AGENTS_LOCK:
            if AGENTS:
                sorted_agents = sorted(
                    AGENTS.items(),
                    key=lambda kv: kv[1].get("last_seen_ts", 0),
                    reverse=True
                )
                agent_id = sorted_agents[0][0]
                print(f"   Using verified target IP from AGENTS: {agent_id}")
            else:
                print("   No verified agents in AGENTS dict - falling back to socket hub")

        # Fallback: use socket hub agent if AGENTS dict empty
        if not agent_id:
            with _SOCKET_AGENT_LOCK:
                socket_agents = list(_SOCKET_AGENT_CONNS.keys())
            if socket_agents:
                agent_id = socket_agents[0]
                print(f"   Fallback: using socket hub agent: {agent_id}")
            else:
                print(f"   No agents anywhere!")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "error",
                    "message": "No agents connected. Ensure agent is running and connected."
                }), 503

    # Step 3: Validate agent_id is in socket hub
    with _SOCKET_AGENT_LOCK:
        is_in_socket_hub = agent_id in _SOCKET_AGENT_CONNS
        all_socket_agents = list(_SOCKET_AGENT_CONNS.keys())

    if not is_in_socket_hub:
        print(f"   Requested agent '{agent_id}' NOT in socket hub")
        print(f"   Socket hub has: {all_socket_agents}")
        print(f"{'='*70}\n")
        return jsonify({
            "status": "error",
            "message": f"Target agent {agent_id} is not connected to socket hub.",
            "requested_agent": agent_id,
            "socket_hub_agents": all_socket_agents,
            "hint": f"Restart clienttt.py on {agent_id} and wait for socket connection."
        }), 503

    print(f"   Final agent_id: {agent_id}")

    # Step 4: Send scan command via socket hub
    scan_data = {"username": username, "password": password, "customer_id": customer_id}
    print(f"\nSending scan command via socket hub to agent: {agent_id}")
    print(f"   Data being sent to Pi: {scan_data}")
    print(f"   customer_id value: {customer_id} (type: {type(customer_id).__name__})")

    sock_resp = socket_hub_send_command(
        agent_id=agent_id,
        command="scan",
        data=scan_data,
        timeout=600
    )

    print(f"   Socket response: {sock_resp}")

    # Handle async scan acknowledgment (scan_started: True)
    if sock_resp and sock_resp.get("status") == "ok":
        payload = sock_resp.get("payload") or sock_resp.get("data") or {}

        if isinstance(payload, dict) and payload.get("scan_started") is True:
            print(f"   Scan started asynchronously - polling for results...")

            scan_results = _wait_for_scan_results(
                agent_id=agent_id,
                username=username,
                password=password,
                max_wait_seconds=600,
                poll_interval=2
            )

            if scan_results:
                print(f"   SCAN SUCCESS! Found {scan_results.get('count', 0)} devices")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "ok",
                    "count": scan_results.get("count", 0),
                    "devices": scan_results.get("devices", []),
                    "source": "socket_hub_async"
                }), 200
            else:
                print(f"   Scan results timeout")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "error",
                    "message": "Scan timeout - agent is still scanning or no cameras found.",
                    "hint": "Scan may still be running. Try again in a few moments.",
                    "source": "socket_hub_async"
                }), 504

        # Sync response with devices in payload
        if isinstance(payload, dict) and "devices" in payload:
            print(f"   SCAN SUCCESS (sync)! Found {payload.get('count', 0)} devices")
            print(f"{'='*70}\n")
            return jsonify({
                "status": "ok",
                "count": payload.get("count", 0),
                "devices": payload.get("devices", []),
                "source": "socket_hub_sync"
            }), 200

        # Sync response devices at top level
        if "count" in sock_resp and "devices" in sock_resp:
            print(f"   SCAN SUCCESS (sync)! Found {sock_resp.get('count', 0)} devices")
            print(f"{'='*70}\n")
            return jsonify({
                "status": "ok",
                "count": sock_resp.get("count", 0),
                "devices": sock_resp.get("devices", []),
                "source": "socket_hub_sync"
            }), 200

    # Handle error response from agent
    if sock_resp and sock_resp.get("status") == "error":
        error_msg = sock_resp.get("message", "Unknown error")
        print(f"   AGENT RETURNED ERROR: {error_msg}")
        print(f"{'='*70}\n")

        if "timeout" in error_msg.lower():
            return jsonify({
                "status": "error",
                "message": "Scan timeout - agent is busy or scanning a large network.",
                "details": error_msg,
                "hint": "Try again in a few seconds.",
                "source": "socket_hub"
            }), 504

        return jsonify({
            "status": "error",
            "message": f"Agent error: {error_msg}",
            "hint": "Check agent logs for more details.",
            "source": "socket_hub"
        }), 503

    # No response at all
    print(f"   No response from agent (timeout or disconnected)")
    print(f"{'='*70}\n")
    return jsonify({
        "status": "error",
        "message": "Agent did not respond to scan command (socket timeout).",
        "agent_id": agent_id,
        "hint": "Agent may have disconnected. Check agent logs and restart if needed.",
        "source": "socket_hub"
    }), 503


@api_bp.route("/scan_results", methods=["POST"])
def api_scan_results():
    """
    Agent POSTs scan results here after completing async scan.
    """
    data = request.get_json(silent=True) or {}

    agent_id = (data.get("agent_id") or "").strip()
    username = (data.get("username") or "").strip()
    password_hash = (data.get("password_hash") or "").strip()
    count = data.get("count", 0)
    devices = data.get("devices", [])
    user_id_from_pi = data.get("user_id")

    print(f"\n{'='*70}")
    print(f"SCAN RESULTS RECEIVED FROM PI")
    print(f"   Agent ID: {agent_id}")
    print(f"   Username: {username}")
    print(f"   Password Hash: {password_hash}")
    print(f"   Count: {count}")
    print(f"   user_id (top-level from Pi): {user_id_from_pi}")
    print(f"   user_id type: {type(user_id_from_pi).__name__}")
    if devices:
        for i, dev in enumerate(devices[:3]):
            print(f"   Device[{i}] user_id: {dev.get('user_id', 'NOT SET')} | ip: {dev.get('ip', dev.get('ip_address', 'N/A'))}")
        if len(devices) > 3:
            print(f"   ... and {len(devices) - 3} more devices")
    print(f"   Full payload keys: {list(data.keys())}")
    print(f"{'='*70}")

    if not agent_id or not username or not password_hash:
        print(f"   Missing required fields")
        print(f"{'='*70}\n")
        return jsonify({
            "status": "error",
            "message": "Missing required fields: agent_id, username, password_hash"
        }), 400

    # Store results for polling
    scan_key = f"{agent_id}:{username}:{password_hash}"

    with SCAN_RESULTS_LOCK:
        SCAN_RESULTS[scan_key] = {
            "count": count,
            "devices": devices,
            "timestamp": time.time()
        }

    print(f"   Scan results stored with key: {scan_key}")
    print(f"{'='*70}\n")

    return jsonify({
        "status": "ok",
        "message": "Scan results received and stored"
    }), 200


@api_bp.route("/scan-db", methods=["POST"])
def api_scan_db():
    """
    SOCKET VERSION: Sends complete scan data via socket to agent for database save
    """
    try:
        payload = request.get_json(silent=True) or {}
        devices = payload.get("devices", [])

        if not isinstance(devices, list) or len(devices) == 0:
            return jsonify({"ok": False, "message": "No devices provided"}), 400

        # Get TARGET_IP and agent_id from database
        TARGET_IP = None
        agent_id = None
        try:
            with psycopg2.connect(_build_dsn()) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Single JOIN instead of two sequential queries
                    cur.execute("""
                        SELECT si.ip_address
                        FROM public.device_master dm
                        JOIN public.system_information si
                            ON si.serial_number = dm.serial_number
                            AND si.ip_address IS NOT NULL
                        WHERE dm.serial_number IS NOT NULL
                        ORDER BY dm.updated_at DESC, si.created_at DESC
                        LIMIT 1
                    """)
                    row = cur.fetchone()

                    if row:
                        ip_data = row['ip_address']

                        if isinstance(ip_data, dict):
                            for key, value in ip_data.items():
                                if isinstance(value, str) and value.count('.') == 3 and not value.startswith('127.'):
                                    TARGET_IP = value
                                    agent_id = value
                                    break
                        else:
                            if isinstance(ip_data, str) and not ip_data.startswith('127.'):
                                TARGET_IP = ip_data
                                agent_id = ip_data

                        print(f"   TARGET_IP: {TARGET_IP}, agent_id: {agent_id}")
        except Exception as e:
            print(f"Database error: {e}")
            return jsonify({
                "ok": False,
                "message": "Failed to query target IP from database",
                "error": str(e)
            }), 500

        if not TARGET_IP or not agent_id:
            return jsonify({
                "ok": False,
                "message": "No valid target IP found in database"
            }), 404

        # Send COMPLETE scan data as received
        user_id = payload.get("user_id")
        scan_data = {
            "devices": devices,
            "user_id": user_id,
            "timestamp": time.time(),
            "source": "distributor_dashboard"
        }

        print("=" * 80)
        print(f"Total devices: {len(devices)}")
        print(f"Target agent_id: {agent_id}")
        print(f"Sending complete scan data via socket...")
        print(f"Sample device: {json.dumps(devices[0] if devices else {}, indent=2)}")
        print("=" * 80)

        # Send complete scan data via socket
        try:
            print(f"\nSending 'receive_scan_data' command to agent {agent_id}...")

            socket_response = socket_hub_send_command(
                agent_id=agent_id,
                command="receive_scan_data",
                data=scan_data,
                timeout=90
            )

            print(f"Socket response received:")
            print(json.dumps(socket_response, indent=2))

            if socket_response.get("ok") or socket_response.get("status") == "ok":
                agent_data = socket_response.get("data") or {}

                saved_count = agent_data.get("saved_count", 0)
                total_analytics = agent_data.get("total_analytics", 0)

                return jsonify({
                    "ok": True,
                    "message": "Scan data sent and saved successfully via socket",
                    "total_devices": len(devices),
                    "saved_count": saved_count,
                    "total_analytics": total_analytics,
                    "agent_response": agent_data,
                    "method": "socket",
                    "agent_id": agent_id
                }), 200
            else:
                error_msg = socket_response.get("error") or socket_response.get("message", "Unknown error")
                print(f"Socket command failed: {error_msg}")

                return jsonify({
                    "ok": False,
                    "message": f"Failed to save scan data: {error_msg}",
                    "method": "socket",
                    "agent_id": agent_id,
                    "socket_response": socket_response
                }), 500

        except Exception as e:
            print(f"Socket error: {str(e)}")
            traceback.print_exc()

            return jsonify({
                "ok": False,
                "message": "Socket communication failed",
                "error": str(e),
                "agent_id": agent_id
            }), 500

    except Exception as e:
        print(f"Fatal error in api_scan_db:")
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "message": "Internal server error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ── Static IP Discovery ─────────────────────────────────────────────────────
@api_bp.route("/static-ip-discovery", methods=["POST"])
def static_ip_discovery():
    """Discover cameras at a static IP using ONVIF/ISAPI."""
    from nvr_rtsp_fetch import discover_onvif_cameras

    data = request.get_json(force=True)
    ip = (data.get("ip") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    port = int(data.get("port") or 80)
    rtsp_port = int(data.get("rtsp_port") or 554)

    if not ip or not username or not password:
        return jsonify({"status": "error", "message": "IP, username, and password are required"}), 400

    try:
        result = discover_onvif_cameras(ip, port, username, password, rtsp_port)
        print(result,'---------------------------------------------------------------------')

        devices = []
        for cam in result.get("cameras", []):
            rtsp_url = (
                cam.get("rtsp_sub")
                or cam.get("rtsp_url")
                or cam.get("rtsp_main")
                or ""
            )
            devices.append({
                "channel": cam.get("channel", ""),
                "name": cam.get("name") or cam.get("label", ""),
                "ip": ip,
                "rtsp_url": rtsp_url,
                "rtsp_main": cam.get("rtsp_main", ""),
                "rtsp_sub": cam.get("rtsp_sub", ""),
                "snapshot_url": cam.get("snapshot_url", ""),
                "profile": cam.get("profile", ""),
                "resolution": cam.get("resolution", ""),
                "encoding": cam.get("encoding", ""),
            })

        if not result.get("success") and not devices:
            return jsonify({
                "status": "error",
                "message": result.get("error") or "Discovery failed",
                "details": result.get("error_details", []),
                "devices": [],
            }), 200

        return jsonify({
            "status": "ok",
            "devices": devices,
            "manufacturer": result.get("manufacturer"),
            "model": result.get("model"),
            "onvif_port": port,
            "rtsp_port": result.get("rtsp_port"),
            "camera_count": len(devices),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ── User Purchases / Analytics Quota (stub) ──────────────────────────────────
@api_bp.route("/dealer/user-purchases2", methods=["GET"])
def api_dealer_user_purchases2():
    """
    Proxy endpoint for customer analytics purchase data.
    Keeps browser requests same-origin and avoids CORS issues.
    """
    try:
        user_id = (request.args.get('user_id') or '').strip()
        if not user_id:
            return jsonify({
                "status": "error",
                "message": "user_id is required",
                "error_code": "MISSING_USER_ID"
            }), 400

        analytics_url = f"{Config.EXTERNAL_USER_PURCHASES}?user_id={user_id}"
        response = http_requests.get(analytics_url, timeout=30)
        data = response.json() if response.content else {}

        if response.status_code != 200:
            return jsonify({
                "status": "error",
                "message": data.get('message') or f"API error: {response.status_code}",
                "error_code": "EXTERNAL_API_ERROR",
                "details": data
            }), response.status_code

        return jsonify(data), 200

    except Exception as e:
        print(f"❌ Customer analytics proxy error: {e}")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Failed to fetch customer analytics",
            "error_code": "INTERNAL_ERROR"
        }), 500


# ── Save Analytics (FRS + other) ─────────────────────────────────────────
FRS_ANALYTICS_NAMES = {"frs", "unknown person alert", "unknown person"}

@api_bp.route("/save-analytics", methods=["POST"])
def api_save_analytics():
    """
    Save selected analytics for discovered devices.

    Splits into two external API calls:
      - FRS / Unknown Person Alert  → https://api.pgak.co.in/analytics/frs_insert
      - All other analytics         → https://api.pgak.co.in/analytics/insert_analytics

    Looks up pi_serial from device_master → system_information.
    Falls back to None if not found.
    """
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        port = data.get("port", 554)
        devices = data.get("devices", [])

        if not user_id:
            return jsonify({"status": "error", "message": "user_id is required"}), 400

        # Ensure user_id and port are integers for external APIs
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "user_id must be a number"}), 400
        try:
            port = int(port)
        except (ValueError, TypeError):
            port = 554
        if not devices:
            return jsonify({"status": "error", "message": "No devices with analytics provided"}), 400

        # ── Resolve pi_serial from DB (single JOIN query) ──────────────
        pi_serial = None
        try:
            with psycopg2.connect(**Config.DB_CONFIG) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT dm.serial_number
                        FROM public.device_master dm
                        WHERE dm.serial_number IS NOT NULL
                        ORDER BY dm.updated_at DESC
                        LIMIT 1
                    """)
                    row = cur.fetchone()
                    if row and row[0]:
                        pi_serial = row[0]
        except Exception as e:
            logger.warning("Could not resolve pi_serial: %s", e)

        logger.info("save-analytics: user_id=%s, pi_serial=%s, devices=%d",
                     user_id, pi_serial, len(devices))

        # ── Split devices into FRS cameras and other analytics ───────────
        frs_cameras = []
        other_cameras = []

        for dev_idx, dev in enumerate(devices):
            ip = dev.get("ip") or dev.get("device_ip") or ""
            substream = dev.get("substream_rtsp") or dev.get("rtsp_url") or ""
            mainstream = dev.get("mainstream_rtsp") or ""
            devices_id = dev.get("devices_id")
            # External API requires devices_id as integer; use index+1 as fallback
            if devices_id is not None:
                try:
                    devices_id = int(devices_id)
                except (ValueError, TypeError):
                    devices_id = dev_idx + 1
            else:
                devices_id = dev_idx + 1

            for sel in dev.get("analytics", []):
                analytics_name = (sel.get("analyticsType") or "").strip()
                if not analytics_name:
                    continue

                if analytics_name.lower() in FRS_ANALYTICS_NAMES:
                    frs_cameras.append({
                        "devices_id": devices_id,
                        "channel": sel.get("channel") or "ch1",
                        "ip_address": ip,
                        "substream_rtsp": substream,
                        "mainstream_rtsp": mainstream,
                        "nick_Name": dev.get("nick_Name") or dev.get("nickname") or f"Cam-{devices_id}",
                        "city_name": dev.get("city_name") or os.getenv("DEFAULT_CITY_NAME", "NA"),
                        "center_name": dev.get("center_name") or os.getenv("DEFAULT_CENTER_NAME", "NA"),
                        "camera_type": analytics_name,
                        "state_name": dev.get("state_name") or os.getenv("DEFAULT_STATE_NAME", "NA")
                    })
                else:
                    other_cameras.append({
                        "analytics_name": analytics_name,
                        "camera_rtsp": substream
                    })

        results = {"frs_insert": None, "insert_analytics": None}

        # ── Call frs_insert API ──────────────────────────────────────────
        if frs_cameras:
            frs_payload = {
                "user_id": user_id,
                "port": port,
                "pi_serial": pi_serial,
                "cameras": frs_cameras
            }
            logger.info("Calling frs_insert with %d cameras", len(frs_cameras))
            print(f"[FRS_INSERT] Payload: {json.dumps(frs_payload, indent=2)}")
            try:
                resp = http_requests.post(
                    Config.EXTERNAL_FRS_INSERT,
                    json=frs_payload,
                    timeout=30
                )
                results["frs_insert"] = {
                    "status": resp.status_code,
                    "data": resp.json() if resp.content else {}
                }
                logger.info("frs_insert response: %s", resp.status_code)
            except Exception as e:
                logger.error("frs_insert API error: %s", e)
                results["frs_insert"] = {"status": 0, "error": str(e)}

        # ── Call insert_analytics API ────────────────────────────────────
        if other_cameras:
            analytics_payload = {
                "user_id": user_id,
                "port": port,
                "pi_serial": pi_serial,
                "cameras": other_cameras
            }
            logger.info("Calling insert_analytics with %d cameras", len(other_cameras))
            try:
                resp = http_requests.post(
                    Config.EXTERNAL_INSERT_ANALYTICS,
                    json=analytics_payload,
                    timeout=30
                )
                results["insert_analytics"] = {
                    "status": resp.status_code,
                    "data": resp.json() if resp.content else {}
                }
                logger.info("insert_analytics response: %s", resp.status_code)
            except Exception as e:
                logger.error("insert_analytics API error: %s", e)
                results["insert_analytics"] = {"status": 0, "error": str(e)}

        # ── Build response ───────────────────────────────────────────────
        all_ok = True
        errors = []

        if frs_cameras and (not results["frs_insert"] or results["frs_insert"].get("status") not in (200, 201)):
            all_ok = False
            errors.append("FRS insert failed: " + str(results["frs_insert"]))

        if other_cameras and (not results["insert_analytics"] or results["insert_analytics"].get("status") not in (200, 201)):
            all_ok = False
            errors.append("Analytics insert failed: " + str(results["insert_analytics"]))

        if all_ok:
            return jsonify({
                "status": "success",
                "message": f"Analytics saved: {len(frs_cameras)} FRS, {len(other_cameras)} other",
                "results": results
            }), 200
        else:
            return jsonify({
                "status": "partial" if (frs_cameras and other_cameras) else "error",
                "message": "; ".join(errors),
                "results": results
            }), 207 if (results["frs_insert"] or results["insert_analytics"]) else 500

    except Exception as e:
        logger.error("save-analytics error: %s", e, exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Failed to save analytics",
            "error": str(e)
        }), 500


_PLACEHOLDER_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n"
    b"\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d"
    b"\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b"
    b"\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05"
    b"\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03"
    b"\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03"
    b"\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05"
    b"\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0"
    b"$3br\x82\t\n\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZcdefghij"
    b"stuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98"
    b"\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7"
    b"\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6"
    b"\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3"
    b"\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb"
    b"\xd0\xff\xd9"
)


def _placeholder_response():
    return Response(_PLACEHOLDER_JPEG, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


def _sanitize_rtsp_url(rtsp_url: str) -> str:
    """Re-encode userinfo so passwords containing '@', ':', '/', etc. don't break parsing."""
    try:
        if not rtsp_url.lower().startswith("rtsp://"):
            return rtsp_url
        rest = rtsp_url[7:]
        if "@" not in rest:
            return rtsp_url
        userinfo, _, hostpart = rest.rpartition("@")
        if ":" in userinfo:
            user, _, pwd = userinfo.partition(":")
        else:
            user, pwd = userinfo, ""
        user_q = quote(user, safe="")
        pwd_q = quote(pwd, safe="")
        auth = f"{user_q}:{pwd_q}" if pwd else user_q
        return f"rtsp://{auth}@{hostpart}"
    except Exception:
        return rtsp_url


def _inject_channel_into_rtsp(rtsp_url: str, channel: str | int | None) -> str:
    if not channel:
        return rtsp_url
    try:
        parsed = urlparse(rtsp_url)
        path = parsed.path or ""
        if "Streaming/Channels/" in path:
            import re as _re
            path = _re.sub(r"(Streaming/Channels/)(\d+)", lambda m: f"{m.group(1)}{int(channel)}02", path)
            return urlunparse(parsed._replace(path=path))
    except Exception:
        pass
    return rtsp_url


@api_bp.route("/static-ip-thumbnail", methods=["GET"])
def static_ip_thumbnail():
    rtsp_url = (request.args.get("rtsp_url") or "").strip()
    channel = request.args.get("channel")

    print(rtsp_url,channel,'-------------------------------------------------------------------------------')

    if not rtsp_url or not rtsp_url.lower().startswith("rtsp://"):
        return _placeholder_response()

    rtsp_url = _sanitize_rtsp_url(rtsp_url)
    rtsp_url = _inject_channel_into_rtsp(rtsp_url, channel)
    print(rtsp_url,'====================================================================')

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-stimeout", "3000000",
        "-analyzeduration", "1000000",
        "-probesize", "500000",
        "-i", rtsp_url,
        "-frames:v", "1",
        "-q:v", "8",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=6,
        )
        if proc.returncode == 0 and proc.stdout[:2] == b"\xff\xd8":
            return Response(
                proc.stdout,
                mimetype="image/jpeg",
                headers={"Cache-Control": "public, max-age=30"},
            )
        logger.warning("static-ip-thumbnail ffmpeg failed rc=%s stderr=%s", proc.returncode, proc.stderr[-400:])
    except subprocess.TimeoutExpired:
        logger.warning("static-ip-thumbnail timeout for %s", rtsp_url)
    except Exception as e:
        logger.error("static-ip-thumbnail error: %s", e)

    return _placeholder_response()

