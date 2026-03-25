import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from datetime import datetime, timedelta, timezone
from flask import send_from_directory, abort
import time
import requests
from email.message import EmailMessage
import threading
import passlib.handlers.bcrypt   # dynamic import
import hashlib
import uuid
import xml.etree.ElementTree as ET
import base64
import os
import urllib3
import netifaces
from urllib.parse import urlparse
from requests.auth import HTTPDigestAuth
import socket
import psycopg2
import random
import string
import hashlib
from passlib.context import CryptContext
from psycopg2.extras import Json, RealDictCursor
import os
import smtplib
import traceback
import ssl
from flask_cors import CORS
import re
from sqlalchemy import text
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import csv
import json
import socket
import traceback
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# ✅ FIX 1: Fixed secret_key (os.urandom badata tha har restart pe session expire)
# .env mein SECRET_KEY=koi-bhi-lamba-string daalo
app.secret_key = os.getenv("SECRET_KEY", "pgak-fixed-secret-key-change-this-in-production-2024")

# ✅ FIX 2: Session lifetime globally set karo (pehle ye login route ke andar tha - galat jagah)
app.permanent_session_lifetime = timedelta(hours=24)

CORS(app, supports_credentials=True)

# ======================================================================================
# SOCKET HUB (Reverse connection from local agents -> Azure server)
# Why: Azure server CANNOT reach private LAN IPs like 192.168.x.x. Agents must connect out.
# Protocol: newline-delimited JSON (one JSON object per line).
# Agent sends: {"type":"hello","agent_id":"192.168.1.111","hostname":"pi","agent_ip":"192.168.1.111"}
# Server sends: {"type":"command","request_id":"...","command":"scan_with_credentials","data":{...}}
# Agent replies: {"type":"response","request_id":"...","ok":true,"payload":{...}}
# ======================================================================================

SOCKET_HUB_BIND = os.getenv("SOCKET_HUB_BIND", "0.0.0.0").strip() or "0.0.0.0"
SOCKET_HUB_PORT = int(os.getenv("SOCKET_HUB_PORT", "5006"))  # ✅ FIXED: Changed from 7000 to 5006
SOCKET_HUB_READ_TIMEOUT = int(os.getenv("SOCKET_HUB_READ_TIMEOUT", "120"))  # ✅ FIXED: Changed from 5 to 120 seconds
SOCKET_HUB_DEFAULT_CMD_TIMEOUT = int(os.getenv("SOCKET_HUB_DEFAULT_CMD_TIMEOUT", "45"))

_SOCKET_AGENT_LOCK = threading.Lock()
_SOCKET_AGENT_CONNS = {}   # agent_id -> {"sock": socket.socket, "wlock": Lock, "last_seen": float, ...}

_PENDING_LOCK = threading.Lock()
_PENDING = {}  # request_id -> {"event": Event, "resp": dict or None}


def _sock_send_json_line(sock_obj, wlock, obj):
    line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8", errors="ignore")
    with wlock:
        sock_obj.sendall(line)

def _sock_register_agent(agent_id, sock_obj, meta):
    with _SOCKET_AGENT_LOCK:
        old = _SOCKET_AGENT_CONNS.get(agent_id)
        if old and old.get("sock") is not sock_obj:
            try:
                old["sock"].close()
            except Exception:
                pass
        _SOCKET_AGENT_CONNS[agent_id] = {
            "sock": sock_obj,
            "wlock": threading.Lock(),
            "last_seen": time.time(),
            **(meta or {})
        }

def _sock_drop_agent(agent_id):
    with _SOCKET_AGENT_LOCK:
        info = _SOCKET_AGENT_CONNS.pop(agent_id, None)
    if info and info.get("sock"):
        try:
            info["sock"].close()
        except Exception:
            pass

def _sock_set_pending_response(request_id, resp):
    with _PENDING_LOCK:
        item = _PENDING.get(request_id)
        if item:
            item["resp"] = resp
            item["event"].set()

def socket_hub_send_command(agent_id, command, data, timeout=None):
    """Send command to agent over socket and wait for response."""
    if not agent_id:
        return {"ok": False, "error": "agent_id missing"}

    with _SOCKET_AGENT_LOCK:
        conn = _SOCKET_AGENT_CONNS.get(agent_id)

    if not conn:
        return {"ok": False, "error": "agent not connected on socket: %s" % agent_id}

    sock_obj = conn["sock"]
    wlock = conn["wlock"]

    req_id = str(uuid.uuid4())
    evt = threading.Event()

    with _PENDING_LOCK:
        _PENDING[req_id] = {"event": evt, "resp": None}

    cmd_payload = {
        "type": "command",
        "request_id": req_id,
        "command": command,
        "data": data or {},
        "ts": time.time(),
    }
    print(f"   📤 [SOCKET_HUB] Sending to {agent_id}: command={command}, data keys={list((data or {}).keys())}")
    print(f"   📤 [SOCKET_HUB] Full payload: {cmd_payload}")

    try:
        _sock_send_json_line(sock_obj, wlock, cmd_payload)
        print(f"   ✅ [SOCKET_HUB] Command sent successfully to {agent_id}")
    except Exception as e:
        with _PENDING_LOCK:
            _PENDING.pop(req_id, None)
        return {"ok": False, "error": "failed to send command: %s" % e}

    if timeout is None:
        timeout = SOCKET_HUB_DEFAULT_CMD_TIMEOUT

    ok = evt.wait(timeout)
    with _PENDING_LOCK:
        item = _PENDING.pop(req_id, None)

    if (not ok) or (not item) or (item.get("resp") is None):
        return {"ok": False, "error": "timeout waiting response from agent %s (cmd=%s)" % (agent_id, command)}

    return item["resp"]

def _socket_client_reader(sock_obj, addr):
    agent_id = None
    try:
        sock_obj.settimeout(SOCKET_HUB_READ_TIMEOUT)
        f = sock_obj.makefile("r", encoding="utf-8", errors="ignore")
        while True:
            try:
                line = f.readline()
            except Exception:
                line = ""
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except Exception:
                continue

            mtype = (msg.get("type") or "").strip().lower()
            if mtype == "hello":
                agent_id = (msg.get("agent_id") or msg.get("agent_ip") or msg.get("ip") or "").strip()
                if not agent_id:
                    continue
                _sock_register_agent(agent_id, sock_obj, {
                    "addr": addr,
                    "hostname": msg.get("hostname"),
                    "agent_ip": msg.get("agent_ip"),
                    "agent_port": msg.get("agent_port"),
                })
            elif mtype == "response":
                rid = (msg.get("request_id") or "").strip()
                if rid:
                    _sock_set_pending_response(rid, msg)
            elif mtype == "heartbeat":
                aid = (msg.get("agent_id") or msg.get("agent_ip") or "").strip()
                if aid:
                    _sock_register_agent(aid, sock_obj, {"addr": addr})

            if agent_id:
                with _SOCKET_AGENT_LOCK:
                    if agent_id in _SOCKET_AGENT_CONNS:
                        _SOCKET_AGENT_CONNS[agent_id]["last_seen"] = time.time()

    finally:
        if agent_id:
            _sock_drop_agent(agent_id)
        try:
            sock_obj.close()
        except Exception:
            pass

def _socket_hub_accept_loop():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((SOCKET_HUB_BIND, SOCKET_HUB_PORT))
    srv.listen(200)
    print("✅ Socket hub listening on %s:%s" % (SOCKET_HUB_BIND, SOCKET_HUB_PORT))

    while True:
        try:
            client_sock, addr = srv.accept()
            t = threading.Thread(target=_socket_client_reader, args=(client_sock, addr), daemon=True)
            t.start()
        except Exception as e:
            print("⚠ Socket hub accept error:", e)
            time.sleep(1)

def start_socket_hub():
    threading.Thread(target=_socket_hub_accept_loop, daemon=True).start()

@app.route("/socket-hub/agents", methods=["GET"])
def socket_hub_agents():
    with _SOCKET_AGENT_LOCK:
        agents = []
        now = time.time()
        for aid, info in _SOCKET_AGENT_CONNS.items():
            agents.append({
                "agent_id": aid,
                "hostname": info.get("hostname"),
                "agent_ip": info.get("agent_ip"),
                "agent_port": info.get("agent_port"),
                "addr": str(info.get("addr")),
                "last_seen_sec": int(now - float(info.get("last_seen") or now)),
            })
    return jsonify({"status": "ok", "count": len(agents), "agents": agents})

# ================================
# DB CONFIG (SERVER ONLY)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "pgak_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "qwer1234")
}
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ================================
# IN-MEMORY STORE
# ================================
DEVICES = {}
OFFLINE_TIMEOUT = 30
DISCOVERED = {}
# HEARTBEAT_TIMEOUT = 8


SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0").strip()
SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))

# CERT_FILE = os.getenv("CERT_FILE", "cert.pem").strip()
# KEY_FILE = os.getenv("KEY_FILE", "key.pem").strip()

DEFAULT_AGENT_SCHEME = os.getenv("DEFAULT_AGENT_SCHEME", "https").strip().lower()
DEFAULT_AGENT_PORT = int(os.getenv("DEFAULT_AGENT_PORT", "5001"))

HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", "20"))

# ================================
# SNAPSHOT_DIR MAPPING BY IP (from .env)
# ================================
def load_snapshot_dir_map():
    """
    Load SNAPSHOT_DIR_MAP from .env file.
    Expected format: JSON string
    
    Example in .env:
    SNAPSHOT_DIR_MAP={"192.168.1.44": "\\\\192.168.1.44\\SharedFolder", "192.168.1.111": "\\\\192.168.1.111\\pi4\\SharedFolder"}
    """
    import json
    
    snapshot_dir_json = os.getenv("SNAPSHOT_DIR_MAP", "{}")
    
    try:
        SNAPSHOT_DIR_MAP = json.loads(snapshot_dir_json)
        print(f"[OK] Loaded SNAPSHOT_DIR_MAP from .env with {len(SNAPSHOT_DIR_MAP)} entries")
        for ip, path in SNAPSHOT_DIR_MAP.items():
            print(f"   {ip} -> {path}")
        return SNAPSHOT_DIR_MAP
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in SNAPSHOT_DIR_MAP environment variable")
        print(f"   Error: {e}")
        print(f"   Using empty mapping as fallback")
        return {}

SNAPSHOT_DIR_MAP = load_snapshot_dir_map()

def get_snapshot_dir(target_ip):
    """
    Get the appropriate SNAPSHOT_DIR based on target IP.
    
    Args:
        target_ip (str): The target IP address
        
    Returns:
        str: The corresponding snapshot directory path, or None if not found
    """
    if not target_ip:
        return None
    
    snapshot_dir = SNAPSHOT_DIR_MAP.get(target_ip)
    if snapshot_dir:
        print(f"   📁 SNAPSHOT_DIR selected for {target_ip}: {snapshot_dir}")
    else:
        print(f"   ⚠️  WARNING: No SNAPSHOT_DIR configured for {target_ip}")
    
    return snapshot_dir

# ================================
# PINCODE LOOKUP ENDPOINT
# ================================
@app.route("/api/pincode-lookup", methods=["POST"])
def pincode_lookup():
    """
    Fetch pincode data including post office, districts_name, city, state
    Accepts both single pincode and multiple pincodes (for distributor)
    Returns matching records for autocomplete/dropdown
    """
    try:
        data = request.get_json(silent=True) or {}
        pincode_input = (data.get("pincode") or "").strip()

        if not pincode_input:
            return jsonify({
                "status": "error",
                "message": "Pincode is required",
                "data": []
            }), 400

        # Remove spaces and special characters, keep only digits
        digits_only = "".join(ch for ch in pincode_input if ch.isdigit())

        if len(digits_only) == 0:
            return jsonify({
                "status": "error",
                "message": "Please enter valid pincode digits",
                "data": []
            }), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Search pincodes that start with the input (for autocomplete)
        # This allows progressive search: 5 -> 50 -> 500 -> 50001, etc.
        cursor.execute("""
            SELECT DISTINCT
                pincode,
                post_office,
                districts_name,
                city,
                state
            FROM pincodes
            WHERE pincode LIKE %s
            ORDER BY pincode ASC
            LIMIT 20
        """, (digits_only + '%',))

        results = cursor.fetchall()
        cursor.close()
        conn.close()

        if results:
            return jsonify({
                "status": "success",
                "message": f"Found {len(results)} pincode(s)",
                "data": results
            }), 200
        else:
            return jsonify({
                "status": "success",
                "message": "No pincodes found",
                "data": []
            }), 200

    except Exception as e:
        print("---- /api/pincode-lookup ERROR ----")
        traceback.print_exc()
        print("-----------------------------------")

        return jsonify({
            "status": "error",
            "message": "Server error during pincode lookup",
            "data": [],
            "error": str(e)
        }), 500


@app.route("/api/pincode-details", methods=["POST"])
def pincode_details():
    """
    Get full details for a specific pincode
    Used when user selects a pincode from dropdown
    """
    try:
        data = request.get_json(silent=True) or {}
        pincode = (data.get("pincode") or "").strip()

        if not pincode:
            return jsonify({
                "status": "error",
                "message": "Pincode is required"
            }), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                pincode,
                post_office,
                districts_name,
                city,
                state
            FROM pincodes
            WHERE pincode = %s
            LIMIT 1
        """, (pincode,))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            return jsonify({
                "status": "success",
                "data": result
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Pincode not found"
            }), 404

    except Exception as e:
        print("---- /api/pincode-details ERROR ----")
        traceback.print_exc()
        print("------------------------------------")

        return jsonify({
            "status": "error",
            "message": "Server error",
            "error": str(e)
        }), 500


# ================================
# SIGNUP ROUTES (Admin / Dealer)
# ================================
import random
import string

def generate_unique_code(user_type):
    code = ''.join(random.choices(string.digits, k=5))
    return code

# def generate_unique_code(user_type):
#     """
#     Generate a unique numerical code based on user type
    
#     Args:
#         user_type: 'dealer' or 'distributor'
        
#     Returns:
#         str: Code in format DEALER-XXXXX or DISTRIBUTOR-XXXXX
#     """
#     prefix = user_type.upper()  # DEALER or DISTRIBUTOR
#     digits = string.digits  # Only digits
    
#     # Generate a 5-digit numeric code
#     code = ''.join(random.choices(digits, k=5))
    
#     return f"{prefix}-{code}"  # Format it as DEALER-XXXXX or DISTRIBUTOR-XXXXX


def code_exists_in_db(code, code_column, cur):
    """Check if code already exists in the database"""
    cur.execute(f"""
        SELECT 1 FROM user_signups
        WHERE {code_column} = %s
        LIMIT 1
    """, (code,))
    return cur.fetchone() is not None


def get_unique_code(user_type, cur, max_attempts=5):
    """
    Generate and verify unique code doesn't exist
    
    Args:
        user_type: 'dealer' or 'distributor'
        cur: Database cursor
        max_attempts: Maximum generation attempts
        
    Returns:
        str: Unique code, or None if generation fails
    """
    # Determine which column to check based on user type
    code_column = 'dealer_code' if user_type == 'dealer' else 'distributor_code'
    
    for attempt in range(max_attempts):
        code = generate_unique_code(user_type)
        if not code_exists_in_db(code, code_column, cur):
            return code
    
    return None


# CORRECTED SIGNUP ROUTE FOR DEALERS
# This version properly captures the distributor code entered by dealers


def get_admin_emails_from_db() -> list[str]:
    """
    Fetch admin emails from database:
    user_signups where user_type='admin' and status='Approved'
    """
    emails: list[str] = []
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT email
                FROM user_signups
                WHERE LOWER(user_type) = 'admin'
                  AND (status IS NULL OR status = 'Approved')
                  AND email IS NOT NULL AND TRIM(email) <> ''
            """)
            rows = cur.fetchall()
            for (em,) in rows:
                em = (em or "").strip()
                if em:
                    emails.append(em)
    finally:
        conn.close()
    return emails


def get_admin_emails() -> list[str]:
    """
    Prefer DB admin emails.
    If DB returns empty, fallback to ENV (ADMIN_EMAIL / ADMIN_EMAILS).
    """
    try:
        db_admins = get_admin_emails_from_db()
        if db_admins:
            return db_admins
    except Exception as e:
        logger.exception("DB admin email fetch failed: %s", e)

    bulk = (os.getenv("ADMIN_EMAILS", "") or "").strip()
    single = (os.getenv("ADMIN_EMAIL", "") or "").strip()
    emails = []
    if bulk:
        emails.extend([e.strip() for e in bulk.split(",") if e.strip()])
    if single and single not in emails:
        emails.append(single)
    return emails


def notify_admin_new_signup(user_type: str, full_name: str, address: str, email: str, phone: str,
                            gst_no: str, company_name: str, pincode: str,
                            distributor_code: str | None, dealer_code: str | None,
                            user_id: int) -> None:
    admins = get_admin_emails()
    print(admins, "-------------------------------")

    if not admins:
        logger.warning("No admin emails found in DB or ENV. Skipping notify.")
        return

    user_type_clean = (user_type or "").strip().lower()
    subject = f"New {user_type_clean.title()} Signup Request (Pending Approval) - UserID {user_id}"

    body = (
        f"Hello Admin,\n\n"
        f"A new signup request has been received and is awaiting approval.\n\n"
        f"User Type     : {user_type_clean}\n"
        f"User ID       : {user_id}\n"
        f"Full Name     : {full_name}\n"
        f"Address       : {address}\n"
        f"Email         : {email}\n"
        f"Phone         : {phone}\n"
        f"Company Name  : {company_name}\n"
        f"GST No        : {gst_no}\n"
        f"Pincode       : {pincode}\n"
    )

    if user_type_clean == "dealer":
        body += f"Distributor Code Entered : {distributor_code or '(not provided)'}\n"
        body += f"Internal Dealer Code     : {dealer_code or '(not generated)'}\n"

    if user_type_clean == "distributor":
        body += f"Generated Distributor Code : {distributor_code or '(not generated)'}\n"

    body += (
        f"\nPlease login to Admin Dashboard to Approve/Reject.\n\n"
        f"Regards,\n"
        f"PGAK System\n"
    )

    for admin_email in admins:
        # ✅ IMPORTANT: Let exception show in logs
        send_email(admin_email, subject, body)

# ================================
# Signup
# ================================

@app.route("/api/dealer/customers", methods=["GET"])
def api_dealer_customers():
    """
    Proxy endpoint - fetches fresh token when needed
    """
    try:
        print("\n" + "="*80)
        print("📞 /api/dealer/customers called")
        print(f"   Session user_type: {session.get('user_type')}")
        print(f"   Session user_id: {session.get('user_id')}")
        print("="*80 + "\n")
        
        # ✅ Check authentication
        if 'user_type' not in session or session['user_type'] != 'dealer':
            return jsonify({
                "status": "error",
                "message": "Unauthorized",
                "error_code": "UNAUTHORIZED"
            }), 401

        # ✅ Function to get fresh access token
        def get_fresh_access_token():
            dealer_email = session.get('dealer_email')
            dealer_password = session.get('dealer_password')
            dealer_code = session.get('dealer_code')
            
            if not all([dealer_email, dealer_password, dealer_code]):
                print("❌ Missing credentials in session")
                return None
            
            print(f"🔑 Fetching fresh access token for {dealer_email}...")
            
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
                
                print(f"🔑 Login response: {login_response.status_code}")
                
                if login_response.status_code == 200:
                    login_data = login_response.json()
                    access_token = login_data.get('access_token')
                    
                    if access_token:
                        print(f"✅ Fresh token: {access_token[:50]}...")
                        session['external_access_token'] = access_token
                        session.modified = True
                        return access_token
                else:
                    print(f"❌ Login failed: {login_response.text[:200]}")
                    
            except Exception as e:
                print(f"❌ Token fetch error: {e}")
                traceback.print_exc()
            
            return None
        
        # ✅ Try with existing token first
        access_token = session.get('external_access_token')
        
        if not access_token:
            print("⚠️ No token in session, getting fresh token...")
            access_token = get_fresh_access_token()
            
            if not access_token:
                return jsonify({
                    "status": "error",
                    "message": "Failed to authenticate",
                    "error_code": "AUTH_FAILED"
                }), 401
        
        print(f"✅ Using token: {access_token[:50]}...")
        
        # ✅ Fetch customers
        customers_url = "https://api.pgak.co.in/auth/dealer/customers"
        
        print(f"📡 Calling external API: {customers_url}")
        
        response = requests.get(
            customers_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            },
            timeout=30
        )
        
        print(f"📡 Response: {response.status_code}")
        
        # ✅ If token expired, get fresh token and retry
        if response.status_code == 401:
            print("⚠️ Token expired, getting fresh token...")
            
            access_token = get_fresh_access_token()
            
            if not access_token:
                return jsonify({
                    "status": "error",
                    "message": "Session expired. Please login again.",
                    "error_code": "TOKEN_EXPIRED"
                }), 401
            
            # ✅ Retry with fresh token
            print("🔄 Retrying with fresh token...")
            response = requests.get(
                customers_url,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )
            
            print(f"📡 Retry response: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ API error: {response.text[:500]}")
            return jsonify({
                "status": "error",
                "message": f"API error: {response.status_code}",
                "error_code": "EXTERNAL_API_ERROR"
            }), response.status_code
        
        data = response.json()
        print(f"✅ Success! Customers: {data.get('count', 0)}")
        
        return jsonify({
            "status": "success",
            "customers": data.get('customers', []),
            "count": data.get('count', 0)
        }), 200
        
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Server error",
            "error_code": "INTERNAL_ERROR"
        }), 500

@app.route("/signup", methods=["POST"])
def signup():
    """
    Unified signup endpoint with admin email notification for dealer/distributor requests.
    Modified: Dealer signup now calls external API to get dealer_code
    """

    import json  # ✅ added (safe local import)

    # ✅ Helper: external API response me se "message" nikalna
    def _extract_external_message(resp):
        # 1) Try JSON
        try:
            data = resp.json()
            if isinstance(data, dict):
                return (
                    data.get("message")
                    or data.get("msg")
                    or data.get("error")
                    or data.get("detail")
                )
        except Exception:
            pass

        # 2) Fallback: raw text se parse try
        try:
            data = json.loads(resp.text or "")
            if isinstance(data, dict):
                return (
                    data.get("message")
                    or data.get("msg")
                    or data.get("error")
                    or data.get("detail")
                )
        except Exception:
            pass

        # 3) Final fallback
        return (resp.text or "").strip() or None

    # Extract form data
    user_type = request.form.get("user_type", "").lower()
    full_name = request.form.get("full_name", "").strip()
    address = request.form.get("address", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    # ===== VALIDATION =====
    if password != confirm_password:
        return jsonify({"status": "error", "message": "Passwords do not match."}), 400

    if len(password) < 8:
        return jsonify({"status": "error", "message": "Password must be at least 8 characters long."}), 400

    hashed_password = pwd_context.hash(password)

    # Extract dealer/distributor specific fields
    if user_type in ["dealer", "distributor"]:
        gst_no = request.form.get("gst_no", "").strip()
        company_name = request.form.get("company_name", "").strip()
        pincode = request.form.get("pincode", "").strip()

        if not gst_no or not company_name or not pincode:
            return jsonify({
                "status": "error",
                "message": "GST Number, Company Name, and Pincode are required for dealers and distributors."
            }), 400
    else:
        gst_no = ""
        company_name = ""
        pincode = ""

    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                # ===== DUPLICATE CHECK =====
                cur.execute("""
                    SELECT 1 FROM user_signups
                    WHERE email = %s OR phone_number = %s
                    LIMIT 1
                """, (email, phone_number))

                if cur.fetchone():
                    return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400

                # ===== ADMIN SIGNUP (Direct Approval) =====
                if user_type == "admin":
                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (full_name, address, phone_number, email, hashed_password, user_type, "Approved"))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # ✅ SEND EMAIL TO NEW ADMIN USER
                    try:
                        subject = f"Admin Account Created Successfully - PGAK System"
                        body = (
                            f"Hello {full_name},\n\n"
                            f"Your admin account has been successfully created and approved.\n\n"
                            f"Account Details:\n"
                            f"Email: {email}\n"
                            f"User ID: {new_user_id}\n"
                            f"User Type: Admin\n\n"
                            f"You can now login to the Admin Dashboard with your credentials.\n\n"
                            f"Regards,\n"
                            f"PGAK System\n"
                        )
                        send_email(email, subject, body)
                        logger.info("Admin account confirmation email sent to %s (user_id=%s)", email, new_user_id)
                    except Exception as e:
                        logger.exception("Failed sending admin confirmation email to %s (user_id=%s): %s", email, new_user_id, e)

                    session["user_type"] = "admin"
                    session["user_id"] = new_user_id
                    session["full_name"] = full_name
                    session["email"] = email

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful. Redirecting to dashboard...",
                        "redirect": url_for("admin_dashboard")
                    })

                # ===== DEALER SIGNUP =====
                elif user_type == "dealer":
                    distributor_code = request.form.get("distributor_code", "").strip()

                    if distributor_code:
                        cur.execute("""
                            SELECT user_id FROM user_signups
                            WHERE distributor_code = %s AND user_type = 'distributor'
                            LIMIT 1
                        """, (distributor_code,))
                        distributor = cur.fetchone()
                        if not distributor:
                            return jsonify({
                                "status": "error",
                                "message": "Invalid distributor code. Please verify and try again."
                            }), 400

                    # ✅ NEW: Call external API to get dealer_code
                    address = request.form.get("address", company_name).strip()

                    external_api_url = "https://api.pgak.co.in/auth/dealer/signup"
                    external_api_payload = {
                        "username": full_name,
                        "email": email,
                        "password": password,
                        "confirm_password": password,
                        "phone_no": phone_number,
                        "address": address,
                        "gst_no": gst_no,
                        "pin_no": pincode,
                        "terms_accepted": True
                    }

                    try:
                        logger.info("Calling external API for dealer signup: %s", external_api_url)

                        api_response = requests.post(
                            external_api_url,
                            json=external_api_payload,
                            timeout=30,
                            headers={"Content-Type": "application/json"}
                        )

                        # ✅ IMPORTANT CHANGE: external error aaya to uska "message" nikaal ke wahi return karo
                        if api_response.status_code not in (200, 201):
                            api_msg = _extract_external_message(api_response) or f"External system error ({api_response.status_code})"

                            # Log: only meaningful message
                            logger.error("External API error (%s): %s", api_response.status_code, api_msg)

                            # External codes ko preserve karo (especially 409 already registered)
                            pass_through_codes = {400, 401, 403, 404, 409, 422}
                            http_code = api_response.status_code if api_response.status_code in pass_through_codes else 502

                            return jsonify({
                                "status": "error",
                                "message": api_msg  # ✅ yaha sirf msg
                            }), http_code

                        # Parse success response
                        try:
                            api_data = api_response.json()
                        except Exception:
                            logger.error("External API success response not JSON: %s", api_response.text)
                            return jsonify({
                                "status": "error",
                                "message": "External system returned invalid response. Please contact support."
                            }), 502

                        dealer_code = api_data.get("dealer_code") or api_data.get("dealerCode") or api_data.get("code")

                        if not dealer_code:
                            logger.error("External API did not return dealer_code: %s", api_data)
                            return jsonify({
                                "status": "error",
                                "message": "External system did not return dealer code. Please contact support."
                            }), 502

                        logger.info("Received dealer_code from external API: %s", dealer_code)

                    except requests.exceptions.Timeout:
                        logger.error("External API timeout for dealer signup")
                        return jsonify({
                            "status": "error",
                            "message": "External system timeout. Please try again later."
                        }), 504

                    except requests.exceptions.RequestException as e:
                        logger.exception("External API request failed: %s", str(e))
                        return jsonify({
                            "status": "error",
                            "message": "Failed to connect to external system. Please try again later."
                        }), 503

                    except Exception as e:
                        logger.exception("Unexpected error calling external API: %s", str(e))
                        return jsonify({
                            "status": "error",
                            "message": "An unexpected error occurred. Please contact support."
                        }), 500

                    # ✅ Save to database with dealer_code from external API
                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, gst_no, company_name,
                         pincode, distributor_code, dealer_code, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (
                        full_name, address, phone_number, email, hashed_password,
                        gst_no, company_name, pincode,
                        distributor_code,
                        dealer_code,
                        user_type, "Pending"
                    ))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # ✅ EMAIL ADMIN: New Dealer Request
                    try:
                        notify_admin_new_signup(
                            user_type="dealer",
                            full_name=full_name,
                            address=address,
                            email=email,
                            phone=phone_number,
                            gst_no=gst_no,
                            company_name=company_name,
                            pincode=pincode,
                            distributor_code=distributor_code,
                            dealer_code=dealer_code,
                            user_id=new_user_id
                        )
                    except Exception as e:
                        logger.exception("Admin notify failed (dealer signup) user_id=%s: %s", new_user_id, e)

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful! Your account is awaiting admin approval.",
                        "user_id": new_user_id,
                        "dealer_code": dealer_code,
                        "user_type": user_type
                    })

                # ===== DISTRIBUTOR SIGNUP (UNCHANGED) =====
                elif user_type == "distributor":
                    distributor_code = get_unique_code("distributor", cur)

                    if distributor_code is None:
                        return jsonify({
                            "status": "error",
                            "message": "Failed to generate distributor code. Please try again."
                        }), 500

                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, gst_no, company_name,
                         pincode, distributor_code, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (
                        full_name, address, phone_number, email, hashed_password,
                        gst_no, company_name, pincode, distributor_code,
                        user_type, "Pending"
                    ))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # ✅ EMAIL ADMIN: New Distributor Request
                    try:
                        notify_admin_new_signup(
                            user_type="distributor",
                            full_name=full_name,
                            address=address,
                            email=email,
                            phone=phone_number,
                            gst_no=gst_no,
                            company_name=company_name,
                            pincode=pincode,
                            distributor_code=distributor_code,
                            dealer_code=None,
                            user_id=new_user_id
                        )
                    except Exception as e:
                        logger.exception("Admin notify failed (distributor signup) user_id=%s: %s", new_user_id, e)

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful. Awaiting admin approval.",
                        "user_id": new_user_id,
                        "generated_code": distributor_code,
                        "code_type": "distributor",
                        "user_type": user_type
                    })

                else:
                    return jsonify({
                        "status": "error",
                        "message": "Invalid user type."
                    }), 400

    except psycopg2.errors.UniqueViolation:
        return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400
    except Exception as e:
        logger.exception("Signup failed: %s", str(e))
        return jsonify({"status": "error", "message": f"Signup failed: {str(e)}"}), 500


# @app.route("/signup", methods=["POST"])
# def signup():
#     """
#     Unified signup endpoint with admin email notification for dealer/distributor requests.
#     Modified: Dealer signup now calls external API to get dealer_code
#     """

#     # Extract form data
#     user_type = request.form.get("user_type", "").lower()
#     full_name = request.form.get("full_name", "").strip()
#     address = request.form.get("address", "").strip()
#     phone_number = request.form.get("phone_number", "").strip()
#     email = request.form.get("email", "").strip()
#     password = request.form.get("password", "")
#     confirm_password = request.form.get("confirm_password", "")

#     # ===== VALIDATION =====
#     if password != confirm_password:
#         return jsonify({"status": "error", "message": "Passwords do not match."}), 400

#     if len(password) < 8:
#         return jsonify({"status": "error", "message": "Password must be at least 8 characters long."}), 400

#     hashed_password = pwd_context.hash(password)

#     # Extract dealer/distributor specific fields
#     if user_type in ["dealer", "distributor"]:
#         gst_no = request.form.get("gst_no", "").strip()
#         company_name = request.form.get("company_name", "").strip()
#         pincode = request.form.get("pincode", "").strip()

#         if not gst_no or not company_name or not pincode:
#             return jsonify({
#                 "status": "error",
#                 "message": "GST Number, Company Name, and Pincode are required for dealers and distributors."
#             }), 400
#     else:
#         gst_no = ""
#         company_name = ""
#         pincode = ""

#     try:
#         with psycopg2.connect(**DB_CONFIG) as conn:
#             with conn.cursor() as cur:
#                 # ===== DUPLICATE CHECK =====
#                 cur.execute("""
#                     SELECT 1 FROM user_signups
#                     WHERE email = %s OR phone_number = %s
#                     LIMIT 1
#                 """, (email, phone_number))

#                 if cur.fetchone():
#                     return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400

#                 # ===== ADMIN SIGNUP (Direct Approval) =====
#                 if user_type == "admin":
#                     cur.execute("""
#                         INSERT INTO user_signups
#                         (full_name, address, phone_number, email, password, user_type, status, created_at)
#                         VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
#                         RETURNING user_id
#                     """, (full_name, address, phone_number, email, hashed_password, user_type, "Approved"))

#                     new_user_id = cur.fetchone()[0]
#                     conn.commit()

#                     # ✅ SEND EMAIL TO NEW ADMIN USER
#                     try:
#                         subject = f"Admin Account Created Successfully - PGAK System"
#                         body = (
#                             f"Hello {full_name},\n\n"
#                             f"Your admin account has been successfully created and approved.\n\n"
#                             f"Account Details:\n"
#                             f"Email: {email}\n"
#                             f"User ID: {new_user_id}\n"
#                             f"User Type: Admin\n\n"
#                             f"You can now login to the Admin Dashboard with your credentials.\n\n"
#                             f"Regards,\n"
#                             f"PGAK System\n"
#                         )
#                         send_email(email, subject, body)
#                         logger.info("Admin account confirmation email sent to %s (user_id=%s)", email, new_user_id)
#                     except Exception as e:
#                         logger.exception("Failed sending admin confirmation email to %s (user_id=%s): %s", email, new_user_id, e)

#                     session["user_type"] = "admin"
#                     session["user_id"] = new_user_id
#                     session["full_name"] = full_name
#                     session["email"] = email

#                     return jsonify({
#                         "status": "success",
#                         "message": "Signup successful. Redirecting to dashboard...",
#                         "redirect": url_for("admin_dashboard")
#                     })

#                 # ===== DEALER SIGNUP =====
#                 elif user_type == "dealer":
#                     distributor_code = request.form.get("distributor_code", "").strip()

#                     if distributor_code:
#                         cur.execute("""
#                             SELECT user_id FROM user_signups
#                             WHERE distributor_code = %s AND user_type = 'distributor'
#                             LIMIT 1
#                         """, (distributor_code,))
#                         distributor = cur.fetchone()
#                         if not distributor:
#                             return jsonify({
#                                 "status": "error",
#                                 "message": "Invalid distributor code. Please verify and try again."
#                             }), 400

#                     # ✅ NEW: Call external API to get dealer_code
#                     # Extract address from company_name or use pincode area as fallback
#                     address = request.form.get("address", company_name).strip()
                    
#                     external_api_url = "https://api.pgak.co.in/auth/dealer/signup"
#                     external_api_payload = {
#                         "username": full_name,
#                         "email": email,
#                         "password": password,
#                         "confirm_password": password,
#                         "phone_no": phone_number,
#                         "address": address,
#                         "gst_no": gst_no,
#                         "pin_no": pincode,
#                         "terms_accepted": True  # Hardcoded as requested
#                     }

#                     try:
#                         logger.info("Calling external API for dealer signup: %s", external_api_url)
                        
#                         # Call external API with timeout
#                         api_response = requests.post(
#                             external_api_url,
#                             json=external_api_payload,
#                             timeout=30,  # 30 second timeout
#                             headers={"Content-Type": "application/json"}
#                         )
                        
#                         # Check if API call was successful
#                         if api_response.status_code != 200 and api_response.status_code != 201:
#                             logger.error("External API returned status %s: %s", api_response.status_code, api_response.text)
#                             return jsonify({
#                                 "status": "error",
#                                 "message": f"Failed to register with external system. Status: {api_response.status_code}"
#                             }), 500

#                         # Parse response
#                         api_data = api_response.json()
                        
#                         # Extract dealer_code from API response
#                         # Adjust the key based on actual API response structure
#                         dealer_code = api_data.get("dealer_code") or api_data.get("dealerCode") or api_data.get("code")
                        
#                         if not dealer_code:
#                             logger.error("External API did not return dealer_code: %s", api_data)
#                             return jsonify({
#                                 "status": "error",
#                                 "message": "External system did not return dealer code. Please contact support."
#                             }), 500
                        
#                         logger.info("Received dealer_code from external API: %s", dealer_code)

#                     except requests.exceptions.Timeout:
#                         logger.error("External API timeout for dealer signup")
#                         return jsonify({
#                             "status": "error",
#                             "message": "External system timeout. Please try again later."
#                         }), 504
                    
#                     except requests.exceptions.RequestException as e:
#                         logger.exception("External API request failed: %s", str(e))
#                         return jsonify({
#                             "status": "error",
#                             "message": "Failed to connect to external system. Please try again later."
#                         }), 503
                    
#                     except Exception as e:
#                         logger.exception("Unexpected error calling external API: %s", str(e))
#                         return jsonify({
#                             "status": "error",
#                             "message": "An unexpected error occurred. Please contact support."
#                         }), 500

#                     # ✅ Save to database with dealer_code from external API
#                     cur.execute("""
#                         INSERT INTO user_signups
#                         (full_name, address, phone_number, email, password, gst_no, company_name,
#                          pincode, distributor_code, dealer_code, user_type, status, created_at)
#                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
#                         RETURNING user_id
#                     """, (
#                         full_name, address, phone_number, email, hashed_password,
#                         gst_no, company_name, pincode,
#                         distributor_code,
#                         dealer_code,  # ✅ Using dealer_code from external API
#                         user_type, "Pending"
#                     ))

#                     new_user_id = cur.fetchone()[0]
#                     conn.commit()

#                     # ✅ EMAIL ADMIN: New Dealer Request
#                     try:
#                         notify_admin_new_signup(
#                             user_type="dealer",
#                             full_name=full_name,
#                             address= address,
#                             email=email,
#                             phone=phone_number,
#                             gst_no=gst_no,
#                             company_name=company_name,
#                             pincode=pincode,
#                             distributor_code=distributor_code,
#                             dealer_code=dealer_code,
#                             user_id=new_user_id
#                         )
#                     except Exception as e:
#                         logger.exception("Admin notify failed (dealer signup) user_id=%s: %s", new_user_id, e)

#                     return jsonify({
#                         "status": "success",
#                         "message": "Signup successful! Your account is awaiting admin approval.",
#                         "user_id": new_user_id,
#                         "dealer_code": dealer_code,  # Return dealer_code to frontend
#                         "user_type": user_type
#                     })

#                 # ===== DISTRIBUTOR SIGNUP (UNCHANGED) =====
#                 elif user_type == "distributor":
#                     distributor_code = get_unique_code("distributor", cur)

#                     if distributor_code is None:
#                         return jsonify({
#                             "status": "error",
#                             "message": "Failed to generate distributor code. Please try again."
#                         }), 500

#                     cur.execute("""
#                         INSERT INTO user_signups
#                         (full_name, address, phone_number, email, password, gst_no, company_name,
#                          pincode, distributor_code, user_type, status, created_at)
#                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
#                         RETURNING user_id
#                     """, (
#                         full_name, address, phone_number, email, hashed_password,
#                         gst_no, company_name, pincode, distributor_code,
#                         user_type, "Pending"
#                     ))

#                     new_user_id = cur.fetchone()[0]
#                     conn.commit()

#                     # ✅ EMAIL ADMIN: New Distributor Request
#                     try:
#                         notify_admin_new_signup(
#                             user_type="distributor",
#                             full_name=full_name,
#                             address =address,
#                             email=email,
#                             phone=phone_number,
#                             gst_no=gst_no,
#                             company_name=company_name,
#                             pincode=pincode,
#                             distributor_code=distributor_code,
#                             dealer_code=None,
#                             user_id=new_user_id
#                         )
#                     except Exception as e:
#                         logger.exception("Admin notify failed (distributor signup) user_id=%s: %s", new_user_id, e)

#                     return jsonify({
#                         "status": "success",
#                         "message": "Signup successful. Awaiting admin approval.",
#                         "user_id": new_user_id,
#                         "generated_code": distributor_code,
#                         "code_type": "distributor",
#                         "user_type": user_type
#                     })

#                 else:
#                     return jsonify({
#                         "status": "error",
#                         "message": "Invalid user type."
#                     }), 400

#     except psycopg2.errors.UniqueViolation:
#         return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400
#     except Exception as e:
#         logger.exception("Signup failed: %s", str(e))
#         return jsonify({"status": "error", "message": f"Signup failed: {str(e)}"}), 500

# ===== BONUS: Admin routes to view and manage codes =====

@app.route("/admin/dealer-codes", methods=["GET"])
def admin_dealer_codes():
    """Get all dealer codes"""
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, full_name, address, email, phone_number, 
                           dealer_code, status, created_at
                    FROM user_signups
                    WHERE user_type = 'dealer'
                    ORDER BY created_at DESC
                """)
                
                columns = ['user_id', 'full_name', 'address', 'email', 'phone_number',
                          'dealer_code', 'status', 'created_at']
                dealers = [dict(zip(columns, row)) for row in cur.fetchall()]
                
                return jsonify({
                    "status": "success",
                    "count": len(dealers),
                    "dealers": dealers
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error fetching dealer codes: {str(e)}"
        }), 500
    
@app.get("/api/dealer-code")
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

    # ✅ Fast path: if already in session
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
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
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

        # ✅ Cache in session
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

@app.get("/api/distributor-code")
def api_me_distributor_code():
    """
    Returns distributor_code for the currently logged-in user.
    Works for distributor login (and optionally admin if needed).
    """
    user_id = session.get("user_id")
    user_type = session.get("user_type")

    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    # If you want ONLY distributor users to access this:
    if user_type not in ("distributor", "admin"):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    # ✅ Fast path: if you already stored it in session at login, return it directly
    session_dist_code = session.get("distributor_code")
    if session_dist_code:
        return jsonify({
            "status": "success",
            "user_id": user_id,
            "user_type": user_type,
            "distributor_code": session_dist_code
        })

    # ✅ Otherwise fetch from DB
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
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

        # ✅ Cache in session so next calls don’t hit DB
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

@app.route("/admin/distributor-codes", methods=["GET"])
def admin_distributor_codes():
    """Get all distributor codes"""
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, full_name, address, email, phone_number, 
                           distributor_code, status, created_at
                    FROM user_signups
                    WHERE user_type = 'distributor'
                    ORDER BY created_at DESC
                """)
                
                columns = ['user_id', 'full_name', 'address', 'email', 'phone_number',
                          'distributor_code', 'status', 'created_at']
                distributors = [dict(zip(columns, row)) for row in cur.fetchall()]
                
                return jsonify({
                    "status": "success",
                    "count": len(distributors),
                    "distributors": distributors
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error fetching distributor codes: {str(e)}"
        }), 500


@app.route("/admin/regenerate-code/<user_type>/<int:user_id>", methods=["POST"])
def regenerate_code(user_type, user_id):
    """
    Regenerate code for a dealer or distributor
    
    Args:
        user_type: 'dealer' or 'distributor'
        user_id: User ID
    """
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if user_type not in ["dealer", "distributor"]:
        return jsonify({
            "status": "error",
            "message": "Invalid user type"
        }), 400

    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                # Verify user exists and is correct type
                cur.execute("""
                    SELECT user_type FROM user_signups WHERE user_id = %s
                """, (user_id,))
                
                result = cur.fetchone()
                if not result or result[0] != user_type:
                    return jsonify({
                        "status": "error",
                        "message": f"User not found or not a {user_type}"
                    }), 404

                # Generate new unique code
                new_code = get_unique_code(user_type, cur)
                if new_code is None:
                    return jsonify({
                        "status": "error",
                        "message": "Failed to generate new code"
                    }), 500

                # Update database
                code_column = 'dealer_code' if user_type == 'dealer' else 'distributor_code'
                cur.execute(f"""
                    UPDATE user_signups
                    SET {code_column} = %s, code_rotated_at = NOW()
                    WHERE user_id = %s
                """, (new_code, user_id))
                conn.commit()

                return jsonify({
                    "status": "success",
                    "message": f"{user_type.capitalize()} code regenerated successfully",
                    "new_code": new_code,
                    "user_id": user_id
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error regenerating code: {str(e)}"
        }), 500

# ================================
# 🎯 NEW ENDPOINT: SAVE QR SCANNED DATA TO DEVICE MASTER
# ================================
# @app.route("/admin/save-device-codes", methods=["POST"])
# def save_device_codes():
#     """
#     Save dealer & distributor mapping for a scanned device (QR)
#     """

#     try:
#         # ==========================
#         # AUTH CHECK
#         # ==========================
#         if 'user_type' not in session or session['user_type'] != 'admin':
#             return jsonify({"status": "error", "message": "Unauthorized"}), 401

#         data = request.get_json(silent=True) or {}

#         dealer_id = data.get("dealer_id")
#         serial_number = data.get("serial_number", "").strip()

#         if not dealer_id or not serial_number:
#             return jsonify({
#                 "status": "error",
#                 "message": "dealer_id and serial_number are required"
#             }), 400

#         conn = psycopg2.connect(**DB_CONFIG)
#         cursor = conn.cursor(cursor_factory=RealDictCursor)

#         # ==========================
#         # STEP 1: FETCH DEALER
#         # ==========================
#         cursor.execute("""
#             SELECT 
#                 user_id,
#                 distributor_code
#             FROM user_signups
#             WHERE user_id = %s
#               AND user_type = 'dealer'
#         """, (dealer_id,))

#         dealer = cursor.fetchone()
#         if not dealer:
#             cursor.close()
#             conn.close()
#             return jsonify({
#                 "status": "error",
#                 "message": "Dealer not found"
#             }), 404

#         # ==========================
#         # STEP 2: FIND DISTRIBUTOR USER_ID
#         # ==========================
#         distributor_id = None

#         if dealer["distributor_code"]:
#             cursor.execute("""
#                 SELECT user_id
#                 FROM user_signups
#                 WHERE distributor_code = %s
#                   AND user_type = 'distributor'
#             """, (dealer["distributor_code"],))

#             distributor = cursor.fetchone()
#             if distributor:
#                 distributor_id = distributor["user_id"]

#         # ==========================
#         # STEP 3: INSERT / UPDATE DEVICE
#         # ==========================
#         cursor.execute("""
#             INSERT INTO device_master (
#                 serial_number,
#                 dealer_id,
#                 distributor_id,
#                 created_at,
#                 updated_at
#             )
#             VALUES (%s, %s, %s, NOW(), NOW())
#             ON CONFLICT (serial_number)
#             DO UPDATE SET
#                 dealer_id = EXCLUDED.dealer_id,
#                 distributor_id = EXCLUDED.distributor_id,
#                 updated_at = NOW()
#             RETURNING device_id
#         """, (
#             serial_number,
#             dealer_id,
#             distributor_id
#         ))

#         device_id = cursor.fetchone()["device_id"]

#         conn.commit()
#         cursor.close()
#         conn.close()

#         return jsonify({
#             "status": "success",
#             "message": "Device mapped successfully",
#             "device_id": device_id,
#             "dealer_id": dealer_id,
#             "distributor_id": distributor_id
#         }), 200

#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({
#             "status": "error",
#             "message": "Failed to save device",
#             "error": str(e)
#         }), 500


# # ================================
# # 🎯 GET DEALER INFO BY ID (for QR scanner display)
# # ================================
# @app.route("/admin/get-dealer-info/<int:dealer_id>", methods=["GET"])
# def get_dealer_info(dealer_id):
#     """
#     Get dealer and distributor information by dealer ID
#     Used to display in QR scanner modal
#     """
#     try:
#         if 'user_type' not in session or session['user_type'] != 'admin':
#             return jsonify({"status": "error", "message": "Unauthorized"}), 401

#         conn = psycopg2.connect(**DB_CONFIG)
#         cursor = conn.cursor(cursor_factory=RealDictCursor)

#         cursor.execute("""
#             SELECT 
#                 user_id,
#                 full_name,
#                 email,
#                 company_name,
#                 dealer_code,
#                 distributor_code,
#                 user_type
#             FROM user_signups
#             WHERE user_id = %s AND user_type = 'dealer'
#         """, (dealer_id,))

#         dealer = cursor.fetchone()
        
#         if not dealer:
#             cursor.close()
#             conn.close()
#             return jsonify({
#                 "status": "error",
#                 "message": "Dealer not found"
#             }), 404

#         # If dealer has distributor code, get distributor details
#         distributor_info = None
#         if dealer['distributor_code']:
#             cursor.execute("""
#                 SELECT user_id, full_name, company_name, email
#                 FROM user_signups
#                 WHERE distributor_code = %s AND user_type = 'distributor'
#             """, (dealer['distributor_code'],))
            
#             distributor_info = cursor.fetchone()

#         cursor.close()
#         conn.close()

#         return jsonify({
#             "status": "success",
#             "dealer": {
#                 "user_id": dealer['user_id'],
#                 "full_name": dealer['full_name'],
#                 "email": dealer['email'],
#                 "company_name": dealer['company_name'],
#                 "dealer_code": dealer['dealer_code'],
#                 "distributor_code": dealer['distributor_code']
#             },
#             "distributor": distributor_info if distributor_info else None
#         }), 200

#     except Exception as e:
#         print("---- /admin/get-dealer-info ERROR ----")
#         traceback.print_exc()
#         print("--------------------------------------")
#         return jsonify({
#             "status": "error",
#             "message": "Failed to fetch dealer info",
#             "error": str(e)
#         }), 500


# ===== IMPORTANT: Enable CORS =====
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# ==============================
# HELPER FUNCTIONS
# ==============================
# def get_local_interfaces():
#     """Get all local network interfaces (excluding loopback)"""
#     interfaces = []
#     for interface in netifaces.interfaces():
#         addrs = netifaces.ifaddresses(interface)
#         if netifaces.AF_INET in addrs:
#             for addr in addrs[netifaces.AF_INET]:
#                 ip = addr.get('addr')
#                 if not ip:
#                     continue
#                 # Avoid loopback/invalid addresses for discovery
#                 if ip.startswith("127.") or ip == "0.0.0.0":
#                     continue
#                 interfaces.append(ip)
#     return interfaces


# def build_probe_message():
#     """Build WS-Discovery Probe message"""
#     message_id = str(uuid.uuid4())
#     probe = f"""<?xml version="1.0" encoding="UTF-8"?>
#     <e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
#         <e:Header>
#             <w:MessageID>uuid:{message_id}</w:MessageID>
#             <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
#             <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
#         </e:Header>
#         <e:Body>
#             <d:Probe>
#                 <d:Types>dn:NetworkVideoTransmitter</d:Types>
#             </d:Probe>
#         </e:Body>
#     </e:Envelope>"""
#     return probe


# def create_ws_security_header(username, password, created_time=None):
#     """Create WS-Security header for SOAP authentication"""
#     if created_time is None:
#         created_time = datetime.now(timezone.utc)
#     created = created_time.strftime("%Y-%m-%dT%H:%M:%SZ")
#     nonce = os.urandom(16)
#     nonce_encoded = base64.b64encode(nonce).decode('utf-8')
#     password_digest = base64.b64encode(
#         hashlib.sha1(nonce + created.encode('utf-8') + password.encode('utf-8')).digest()
#     ).decode('utf-8')

#     return f"""
#     <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
#         <wsse:UsernameToken>
#             <wsse:Username>{username}</wsse:Username>
#             <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password>
#             <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_encoded}</wsse:Nonce>
#             <wsu:Created>{created}</wsu:Created>
#         </wsse:UsernameToken>
#     </wsse:Security>
#     """


# def get_device_time(endpoint_url, username, password):
#     """Fetch device time from ONVIF device"""
#     ws_security = create_ws_security_header(username, password)

#     soap_request = f"""
#     <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
#         <s:Header>{ws_security}</s:Header>
#         <s:Body>
#             <tds:GetSystemDateAndTime/>
#         </s:Body>
#     </s:Envelope>"""

#     headers = {'Content-Type': 'application/soap+xml'}

#     try:
#         response = requests.post(endpoint_url, data=soap_request, headers=headers, verify=False, timeout=10)
#         if response.status_code == 200:
#             root = ET.fromstring(response.content)
#             for utc_time in root.iter('{http://www.onvif.org/ver10/schema}UTCDateTime'):
#                 date = utc_time.find('{http://www.onvif.org/ver10/schema}Date')
#                 time = utc_time.find('{http://www.onvif.org/ver10/schema}Time')
#                 if date is not None and time is not None:
#                     year = int(date.find('{http://www.onvif.org/ver10/schema}Year').text)
#                     month = int(date.find('{http://www.onvif.org/ver10/schema}Month').text)
#                     day = int(date.find('{http://www.onvif.org/ver10/schema}Day').text)
#                     hour = int(time.find('{http://www.onvif.org/ver10/schema}Hour').text)
#                     minute = int(time.find('{http://www.onvif.org/ver10/schema}Minute').text)
#                     second = int(time.find('{http://www.onvif.org/ver10/schema}Second').text)
#                     return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
#     except Exception as e:
#         print(f"Failed to get device time: {e}")
#     return None


# def get_device_information(endpoint_url, username, password, device_time):
#     """Fetch device information (Manufacturer, Model, Serial, etc.)"""
#     ws_security = create_ws_security_header(username, password, device_time)

#     soap_request = f"""
#     <?xml version="1.0" encoding="UTF-8"?>
#     <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
#         <SOAP-ENV:Header>{ws_security}</SOAP-ENV:Header>
#         <SOAP-ENV:Body>
#             <tds:GetDeviceInformation/>
#         </SOAP-ENV:Body>
#     </SOAP-ENV:Envelope>"""

#     headers = {'Content-Type': 'application/soap+xml'}

#     try:
#         response = requests.post(endpoint_url, data=soap_request, headers=headers, verify=False, timeout=10)
#         if response.status_code == 200:
#             root = ET.fromstring(response.content)
#             device_info = {}
#             for elem in root.iter():
#                 if 'Manufacturer' in elem.tag:
#                     device_info['Manufacturer'] = elem.text
#                 elif 'Model' in elem.tag:
#                     device_info['Model'] = elem.text
#                 elif 'HardwareId' in elem.tag:
#                     device_info['HardwareId'] = elem.text
#                 elif 'FirmwareVersion' in elem.tag:
#                     device_info['FirmwareVersion'] = elem.text
#                 elif 'SerialNumber' in elem.tag:
#                     device_info['SerialNumber'] = elem.text

#             device_info['MACAddress'] = get_mac_address(endpoint_url, username, password, device_time)
#             return device_info
#         else:
#             print(f"Failed to fetch device information: HTTP {response.status_code}")
#     except Exception as e:
#         print(f"Exception while fetching device information: {e}")
#     return None


# def get_mac_address(endpoint_url, username, password, device_time):
#     """Fetch MAC address from device"""
#     ws_security = create_ws_security_header(username, password, device_time)

#     soap_request = f"""
#       <?xml version="1.0" encoding="UTF-8"?>
#       <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
#           <SOAP-ENV:Header>{ws_security}</SOAP-ENV:Header>
#           <SOAP-ENV:Body>
#               <tds:GetNetworkInterfaces/>
#           </SOAP-ENV:Body>
#       </SOAP-ENV:Envelope>"""

#     headers = {'Content-Type': 'application/soap+xml'}

#     try:
#         response = requests.post(endpoint_url, data=soap_request, headers=headers, verify=False, timeout=10)
#         if response.status_code == 200:
#             root = ET.fromstring(response.content)
#             for elem in root.iter():
#                 if 'HwAddress' in elem.tag:
#                     return elem.text
#     except Exception as e:
#         print(f"Exception while fetching MAC address: {e}")
#     return "Unknown"


# def get_media_service_url(endpoint_url, username, password, device_time):
#     """Fetch media service URL from device capabilities"""
#     ws_security = create_ws_security_header(username, password, device_time)

#     soap_request = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
#                                    xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
#         <s:Header>{ws_security}</s:Header>
#         <s:Body>
#             <tds:GetCapabilities>
#                 <tds:Category>Media</tds:Category>
#             </tds:GetCapabilities>
#         </s:Body>
#     </s:Envelope>"""

#     headers = {
#         'Content-Type': 'application/soap+xml',
#         'SOAPAction': '"http://www.onvif.org/ver10/device/wsdl/GetCapabilities"'
#     }

#     try:
#         response = requests.post(endpoint_url, data=soap_request, headers=headers, verify=False, timeout=10)
#         if response.status_code == 200:
#             root = ET.fromstring(response.content)
#             for capabilities in root.iter():
#                 if strip_namespace(capabilities.tag) == "Capabilities":
#                     for child in capabilities:
#                         if strip_namespace(child.tag) == "Media":
#                             for sub in child:
#                                 if strip_namespace(sub.tag) == "XAddr" and sub.text:
#                                     return sub.text.strip()
#         elif response.status_code == 401:
#             print(f"Unauthorized: Check credentials for {endpoint_url}")
#     except Exception as e:
#         print(f"Exception while fetching media service URL: {e}")

#     return None


# def get_profiles(media_url, username, password, device_time):
#     """Fetch media profiles from device"""
#     ws_security = create_ws_security_header(username, password, device_time)

#     soap_request = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
#     <s:Header>{ws_security}</s:Header>
#     <s:Body>
#         <trt:GetProfiles/>
#     </s:Body>
# </s:Envelope>"""

#     headers = {
#         'Content-Type': 'application/soap+xml',
#         'SOAPAction': '"http://www.onvif.org/ver10/media/wsdl/GetProfiles"'
#     }

#     profiles = []

#     try:
#         response = requests.post(media_url, data=soap_request, headers=headers, verify=False, timeout=10)
#         if response.status_code == 200:
#             root = ET.fromstring(response.content)
#             for profile in root.iter('{http://www.onvif.org/ver10/media/wsdl}Profiles'):
#                 token = profile.attrib.get('token')
#                 name = profile.find('{http://www.onvif.org/ver10/schema}Name')
#                 profiles.append({'token': token, 'name': name.text if name is not None else 'Unnamed Profile'})
#         else:
#             print(f"Failed to get profiles: HTTP {response.status_code}")
#     except Exception as e:
#         print(f"Exception while fetching profiles: {e}")

#     return profiles


# def get_rtsp_uri(media_url, profile_token, username, password, device_time=None):
#     """
#     Fetch the RTSP URI from ONVIF camera using WS-Security first,
#     then fallback to HTTP Digest authentication if WS-Security fails.
#     """
#     try:
#         ws_security = create_ws_security_header(username, password, device_time) if device_time else create_ws_security_header(username, password)
#     except Exception as e:
#         print(f"⚠ WS-Security header creation failed: {e}")
#         ws_security = create_ws_security_header(username, password)

#     soap_request_ws = f"""<?xml version="1.0" encoding="utf-8"?>
#     <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
#     xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
#     xmlns:tt="http://www.onvif.org/ver10/schema">
#         <soap:Header>{ws_security}</soap:Header>
#         <soap:Body>
#             <trt:GetStreamUri>
#                 <trt:StreamSetup>
#                     <tt:Stream>RTP-Unicast</tt:Stream>
#                     <tt:Transport>
#                         <tt:Protocol>RTSP</tt:Protocol>
#                     </tt:Transport>
#                 </trt:StreamSetup>
#                 <trt:ProfileToken>{profile_token}</trt:ProfileToken>
#             </trt:GetStreamUri>
#         </soap:Body>
#     </soap:Envelope>"""

#     headers = {'Content-Type': 'application/soap+xml'}

#     try:
#         response = requests.post(media_url, data=soap_request_ws, headers=headers, verify=False, timeout=5)
#         if not (response.status_code == 200 and "Fault" not in response.text):
#             soap_request_digest = soap_request_ws.replace(ws_security, "")
#             response = requests.post(
#                 media_url,
#                 data=soap_request_digest,
#                 headers=headers,
#                 auth=HTTPDigestAuth(username, password),
#                 verify=False,
#                 timeout=5
#             )

#         root = ET.fromstring(response.content)
#         ns = {'tt': 'http://www.onvif.org/ver10/schema'}
#         uri_element = root.find('.//tt:Uri', ns)

#         if uri_element is not None and uri_element.text:
#             return uri_element.text.strip()
#         else:
#             print("❌ No RTSP URI found in response.")
#             return None

#     except Exception as e:
#         print(f"❌ Exception fetching RTSP URI: {e}")
#         return None


# def strip_namespace(tag):
#     """Remove XML namespace from tag"""
#     return tag.split("}")[-1] if "}" in tag else tag


# def discover_onvif_devices():
#     """Discover ONVIF devices using WS-Discovery protocol"""
#     multicast_group = ('239.255.255.250', 3702)
#     probe_message = build_probe_message().encode('utf-8')
#     devices = []

#     interfaces = get_local_interfaces()
#     print(f"Found Network Interfaces: {interfaces}")

#     for interface_ip in interfaces:
#         print(f"Sending WS-Discovery probe from {interface_ip}...")

#         sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
#         sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#         sock.settimeout(5)

#         try:
#             sock.bind((interface_ip, 0))
#             sock.sendto(probe_message, multicast_group)

#             while True:
#                 data, addr = sock.recvfrom(4096)
#                 if b'XAddrs' in data:
#                     root = ET.fromstring(data)
#                     for elem in root.iter():
#                         if 'XAddrs' in elem.tag and elem.text:
#                             devices.extend(elem.text.strip().split())
#         except socket.timeout:
#             continue
#         except Exception as e:
#             print(f"Discovery error on interface {interface_ip}: {e}")
#             continue
#         finally:
#             sock.close()

#     # Dedupe
#     devices = list(dict.fromkeys(devices))
#     print(f"Discovered devices: {devices}")
#     return devices


# def test_device_credentials(device_url, username, password):
#     """Test if credentials work for a device"""
#     try:
#         device_time = get_device_time(device_url, username, password)
#         if device_time:
#             return True
#     except:
#         pass
#     return False


# def scan_network_for_matching_devices(username, password, timeout=30):
#     """
#     Scan entire network for ONVIF devices and test each with provided credentials.
#     Returns list of devices where credentials match.
#     """
#     print(f"🔍 Starting network scan for devices matching credentials...")
    
#     # First, discover devices using WS-Discovery
#     discovered_devices = discover_onvif_devices()
    
#     if not discovered_devices:
#         print("⚠ No devices discovered via WS-Discovery")
#         return []

#     matching_devices = []
    
#     # Test each discovered device with provided credentials
#     print(f"\n🔐 Testing {len(discovered_devices)} discovered device(s) with provided credentials...")
    
#     for device_url in discovered_devices:
#         print(f"Testing {device_url}...")
        
#         if test_device_credentials(device_url, username, password):
#             print(f"✓ Credentials matched for {device_url}")
            
#             try:
#                 # Get device time for authenticated requests
#                 device_time = get_device_time(device_url, username, password)
                
#                 # Get device info
#                 device_info = get_device_information(device_url, username, password, device_time)
                
#                 # Get media service URL
#                 media_url = get_media_service_url(device_url, username, password, device_time)
                
#                 # Get profiles if media URL exists
#                 profiles = []
#                 rtsp_profiles = []
                
#                 if media_url:
#                     profiles = get_profiles(media_url, username, password, device_time)
                    
#                     # Get RTSP URIs for each profile
#                     for profile in profiles:
#                         rtsp_url = get_rtsp_uri(media_url, profile['token'], username, password, device_time)
#                         if rtsp_url:
#                             rtsp_profiles.append({
#                                 'name': profile['name'],
#                                 'token': profile['token'],
#                                 'rtsp_url': rtsp_url
#                             })
                
#                 device_data = {
#                     'device_service': device_url,
#                     'device_info': device_info or {},
#                     'media_url': media_url,
#                     'profiles': profiles,
#                     'rtsp_profiles': rtsp_profiles,
#                     'device_time_utc': device_time.isoformat() if device_time else None,
#                     'credentials_valid': True
#                 }
                
#                 matching_devices.append(device_data)
#                 print(f"✓ Successfully scanned {device_url}")
                
#             except Exception as e:
#                 print(f"✗ Error scanning {device_url}: {e}")
#                 continue
#         else:
#             print(f"✗ Credentials failed for {device_url}")
    
#     return matching_devices




# @app.route('/health', methods=['GET'])
# def health():
#     """Health check endpoint"""
#     return jsonify({"status": "ok"})


# @app.route('/api/onvif/discover', methods=['GET'])
# def api_discovers():
#     """
#     GET /api/onvif/discover
#     Discover ONVIF devices on network (no auth required)
#     """
#     try:
#         devices = discover_onvif_devices()
#         return jsonify({
#             "status": "ok",
#             "count": len(devices),
#             "devices": devices
#         })
#     except Exception as e:
#         return jsonify({
#             "status": "error",
#             "message": "Discovery failed",
#             "error": str(e)
#         }), 500


# @app.route('/api/onvif/scan-with-credentials', methods=['POST'])
# def api_scan_with_credentials():
#     """
#     POST /api/onvif/scan-with-credentials
    
#     Request body:
#     {
#         "username": "admin",
#         "password": "password123"
#     }
    
#     Scans network for devices and returns only those where credentials match.
#     """
#     data = request.get_json()
    
#     if not data:
#         return jsonify({
#             "status": "error",
#             "message": "Request body is required"
#         }), 400
    
#     username = data.get('username', '').strip()
#     password = data.get('password', '').strip()
    
#     if not username or not password:
#         return jsonify({
#             "status": "error",
#             "message": "Username and password are required"
#         }), 400
    
#     try:
#         matching_devices = scan_network_for_matching_devices(username, password)
        
#         return jsonify({
#             "status": "ok",
#             "count": len(matching_devices),
#             "devices": matching_devices
#         })
#     except Exception as e:
#         return jsonify({
#             "status": "error",
#             "message": "Scan failed",
#             "error": str(e)
#         }), 500


# @app.route('/api/onvif/device/full', methods=['POST'])
# def api_device_full():
#     """
#     POST /api/onvif/device/full
#     Full device scan with specific device URL
    
#     Request body:
#     {
#         "device_service": "http://192.168.1.100:80/onvif/device_service",
#         "username": "admin",
#         "password": "password123",
#         "include_rtsp": true
#     }
#     """
#     data = request.get_json()
    
#     if not data:
#         return jsonify({
#             "status": "error",
#             "message": "Request body is required"
#         }), 400
    
#     device_service = data.get('device_service', '').strip()
#     username = data.get('username', '').strip()
#     password = data.get('password', '').strip()
#     include_rtsp = data.get('include_rtsp', False)
    
#     if not device_service or not username or not password:
#         return jsonify({
#             "status": "error",
#             "message": "device_service, username, and password are required"
#         }), 400
    
#     try:
#         # Get device time
#         device_time = get_device_time(device_service, username, password)
        
#         if not device_time:
#             return jsonify({
#                 "status": "error",
#                 "message": "Failed to authenticate with device. Check credentials."
#             }), 401
        
#         # Get device info
#         device_info = get_device_information(device_service, username, password, device_time)
        
#         # Get media service URL
#         media_url = get_media_service_url(device_service, username, password, device_time)
        
#         profiles = []
#         rtsp_profiles = []
        
#         if media_url:
#             profiles = get_profiles(media_url, username, password, device_time)
            
#             if include_rtsp:
#                 for profile in profiles:
#                     rtsp_url = get_rtsp_uri(media_url, profile['token'], username, password, device_time)
#                     if rtsp_url:
#                         rtsp_profiles.append({
#                             'name': profile['name'],
#                             'token': profile['token'],
#                             'rtsp_url': rtsp_url
#                         })
        
#         return jsonify({
#             "status": "ok",
#             "device_service": device_service,
#             "device_info": device_info or {},
#             "media_url": media_url,
#             "profiles": profiles,
#             "rtsp_profiles": rtsp_profiles if include_rtsp else [],
#             "device_time_utc": device_time.isoformat() if device_time else None
#         })
    
#     except Exception as e:
#         return jsonify({
#             "status": "error",
#             "message": str(e)
#         }), 500
# ==========================================
# DEVICE VALIDATION AND ISSUANCE ENDPOINT
# ==========================================
@app.route('/api/validate-device-serial', methods=['POST'])
def validate_device_serial():
    """
    Validates if a scanned serial number exists in the system_information table and fetches the IP address.

    Expected JSON payload:
    {
        "serial_number": "ABC123XYZ789"
    }

    Response:
    {
        "success": true/false,
        "message": "Serial found" or "Serial not found",
        "serial_number": "ABC123XYZ789",
        "device_info": { 
            "id": "...",
            "serial_number": "...",
            "ip_address": "...",
            "make": "...",
            "model": "...",
            "created_date": "2026-01-29",
            "created_time": "15:30:45"
        }
    }
    """
    conn = None
    cur = None

    try:
        # Validate request content type
        if not request.is_json:
            return jsonify({
                "success": False,
                "message": "Content-Type must be application/json",
                "error_code": "INVALID_CONTENT_TYPE"
            }), 400

        data = request.get_json(silent=True) or {}

        # ==================== VALIDATION ====================
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

        # =====================================================
        # Check if serial exists in system_information table
        # =====================================================
        conn = psycopg2.connect(**DB_CONFIG)
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
            # Serial number NOT found in system_information table
            logger.warning(f"Serial number validation failed: {serial_number} not found in system_information")
            
            return jsonify({
                "success": False,
                "message": f'Serial number "{serial_number}" not found in system information',
                "error_code": "SERIAL_NOT_FOUND",
                "serial_number": serial_number
            }), 404

        # Serial found - extract and format date and time
        created_at = result[5]
        created_date = None
        created_time = None
        
        if created_at:
            # Extract date (YYYY-MM-DD)
            created_date = created_at.strftime('%Y-%m-%d')
            # Extract time (HH:MM:SS)
            created_time = created_at.strftime('%H:%M:%S')

        # Return device information with separate date and time
        device_info = {
            "id": result[0],
            "serial_number": result[1],
            "ip_address": result[2],
            "make": result[3],
            "model": result[4],
            "created_date": created_date,  # Date: 2026-01-29
            "created_time": created_time   # Time: 15:30:45
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
# @app.route('/api/validate-device-serial', methods=['POST'])
# def validate_device_serial():
#     """
#     Validates if a scanned serial number exists in the system_information table and fetches the IP address.

#     Expected JSON payload:
#     {
#         "serial_number": "ABC123XYZ789"
#     }

#     Response:
#     {
#         "success": true/false,
#         "message": "Serial found" or "Serial not found",
#         "serial_number": "ABC123XYZ789",
#         "device_info": { ... }  // if found
#     }
#     """
#     conn = None
#     cur = None

#     try:
#         # Validate request content type
#         if not request.is_json:
#             return jsonify({
#                 "success": False,
#                 "message": "Content-Type must be application/json",
#                 "error_code": "INVALID_CONTENT_TYPE"
#             }), 400

#         data = request.get_json(silent=True) or {}

#         # ==================== VALIDATION ====================
#         if not data.get("serial_number"):
#             return jsonify({
#                 "success": False,
#                 "message": "serial_number is required",
#                 "error_code": "MISSING_SERIAL_NUMBER"
#             }), 400

#         serial_number = str(data.get("serial_number", "")).strip()

#         if len(serial_number) < 3:
#             return jsonify({
#                 "success": False,
#                 "message": "serial_number must be at least 3 characters",
#                 "error_code": "INVALID_SERIAL_NUMBER"
#             }), 400

#         # =====================================================
#         # Check if serial exists in system_information table
#         # =====================================================
#         conn = psycopg2.connect(**DB_CONFIG)
#         cur = conn.cursor()

#         check_sql = """
#             SELECT 
#                 device_id,
#                 serial_number,
#                 ip_address,  -- Assuming ip_address is a column in system_information table
#                 make,
#                 model,
#                 created_at,
#             FROM public.system_information
#             WHERE serial_number = %s
#             LIMIT 1;
#         """

#         cur.execute(check_sql, (serial_number,))
#         result = cur.fetchone()

#         if result is None:
#             # Serial number NOT found in system_information table
#             logger.warning(f"Serial number validation failed: {serial_number} not found in system_information")
            
#             return jsonify({
#                 "success": False,
#                 "message": f'Serial number "{serial_number}" not found in system information',
#                 "error_code": "SERIAL_NOT_FOUND",
#                 "serial_number": serial_number
#             }), 404

#         # Serial found - return device information
#         device_info = {
#             "id": result[0],
#             "serial_number": result[1],
#             "ip_address": result[2],  # IP address fetched here
#             "make": result[3],
#             "model": result[4],
#             "created_at": result[5].isoformat() if result[5] else None
#         }

#         logger.info(f"Serial number validation successful: {serial_number} found in system_information")

#         return jsonify({
#             "success": True,
#             "message": "Serial number found in system information",
#             "error_code": None,
#             "serial_number": serial_number,
#             "device_info": device_info
#         }), 200

#     except Exception as e:
#         if conn:
#             conn.rollback()
#         logger.error(f"Error validating serial number: {str(e)}", exc_info=True)
#         return jsonify({
#             "success": False,
#             "message": f"Validation failed: {str(e)}",
#             "error_code": "VALIDATION_ERROR"
#         }), 500

#     finally:
#         try:
#             if cur:
#                 cur.close()
#         except Exception:
#             pass
#         try:
#             if conn:
#                 conn.close()
#         except Exception:
#             pass


@app.route('/api/devices/save-from-qr', methods=['POST'])
def save_device_qr():
    conn = None
    cur = None

    try:
        # ==================== VALIDATE REQUEST FORMAT ====================
        if not request.is_json:
            return jsonify({
                "success": False,
                "message": "Content-Type must be application/json",
                "error_code": "INVALID_CONTENT_TYPE"
            }), 400

        data = request.get_json(silent=True) or {}

        # ==================== VALIDATE REQUIRED FIELDS ====================
        required_fields = ["serial_number", "user_id", "user_type"]
        missing_fields = [f for f in required_fields if not data.get(f)]
        
        if missing_fields:
            return jsonify({
                "success": False,
                "message": f"Missing required fields: {', '.join(missing_fields)}",
                "error_code": "MISSING_REQUIRED_FIELDS"
            }), 400

        # ==================== PARSE & VALIDATE INPUT ====================
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

        # ==================== DATABASE CONNECTION ====================
        now = datetime.now(timezone.utc)
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # =====================================================
        # STEP 1: Fetch parent distributor_id and dealer_id based on user_type
        # =====================================================
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

            # Dealer CAN have null parent distributor - this is ALLOWED
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

            # CRITICAL: Customer MUST have a parent distributor
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

            # CRITICAL: Customer MUST have a parent dealer
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

        # =====================================================
        # STEP 2: Check if serial exists in system_information
        # =====================================================
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

        # =====================================================
        # STEP 3: Check if device already exists in device_master
        # =====================================================
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

            # =====================================================
            # STEP 3A: VALIDATION FOR EXISTING DEVICES
            # =====================================================

            # ✓ DISTRIBUTOR ASSIGNMENT LOGIC
            if user_type == "distributor":
                # ❌ CRITICAL: Cannot reassign device to a different distributor
                if assigned_distributor_id is not None and assigned_distributor_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Distributor ID {assigned_distributor_id}. Cannot reassign.',
                        "error_code": "DEVICE_ASSIGNED_TO_DIFFERENT_DISTRIBUTOR",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                # ❌ CRITICAL: Cannot assign to distributor if already has a dealer
                if assigned_dealer_id is not None and not is_admin:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Dealer ID {assigned_dealer_id}. Cannot reassign to Distributor.',
                        "error_code": "DEVICE_HAS_DEALER_ASSIGNMENT",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

            # ✓ DEALER ASSIGNMENT LOGIC - CRITICAL RESTRICTIONS
            elif user_type == "dealer":
                # ❌ REJECT: Dealer trying to claim device already assigned to different dealer
                if assigned_dealer_id is not None and assigned_dealer_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Dealer ID {assigned_dealer_id}. Cannot reassign to Dealer ID {user_id}.',
                        "error_code": "DEVICE_ALREADY_ASSIGNED_DEALER",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                # ❌ REJECT: Cannot reassign device that already has customer
                # (This is DEALER issuing to CUSTOMER flow, once customer has it, dealer cannot take it back)
                if assigned_customer_id is not None:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Customer ID {assigned_customer_id}. Dealer cannot reassign.',
                        "error_code": "DEVICE_HAS_CUSTOMER_ASSIGNMENT",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                # ❌ CRITICAL: Dealer WITHOUT parent distributor CANNOT claim device with any distributor
                if parent_distributor_id is None:
                    # Dealer has NO parent distributor
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
                    # ✓ Dealer HAS parent distributor - must match device's distributor
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

            # ✓ CUSTOMER ASSIGNMENT LOGIC
            elif user_type == "customer":
                # ❌ REJECT: Cannot reassign to different customer
                if assigned_customer_id is not None and assigned_customer_id != user_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device "{serial_number}" is already assigned to Customer ID {assigned_customer_id}. Cannot reassign.',
                        "error_code": "DEVICE_ALREADY_ASSIGNED_CUSTOMER",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                # ✓ VERIFY: If device has distributor, must match customer's parent distributor
                if assigned_distributor_id is not None and assigned_distributor_id != parent_distributor_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f'Device belongs to Distributor ID {assigned_distributor_id}. You (Customer) belong to Distributor ID {parent_distributor_id}. Mismatch.',
                        "error_code": "DISTRIBUTOR_MISMATCH",
                        "serial_number": serial_number,
                        "device_id": device_id
                    }), 409

                # ✓ VERIFY: If device has dealer, must match customer's parent dealer
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

        # =====================================================
        # STEP 4: INSERT vs UPDATE LOGIC
        # =====================================================

        if is_new_device:
            # ✅ NEW DEVICE: INSERT

            if user_type == "distributor":
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (
                    serial_number,
                    None,
                    parent_distributor_id,
                    None,
                    now,
                    now
                ))
                row = cur.fetchone()
                device_id = row[0]
                logger.info(
                    f"✅ Device INSERTED by DISTRIBUTOR - ID: {device_id}, Serial: {serial_number}, Dist: {parent_distributor_id}"
                )

            elif user_type == "dealer":
                # ✅ NEW DEVICE: Dealer creates with OR WITHOUT parent distributor
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (
                    serial_number,
                    user_id,
                    parent_distributor_id,  # Can be null
                    None,
                    now,
                    now
                ))
                row = cur.fetchone()
                device_id = row[0]
                
                if parent_distributor_id:
                    logger.info(
                        f"✅ Device INSERTED by DEALER - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: {parent_distributor_id}"
                    )
                else:
                    logger.info(
                        f"✅ Device INSERTED by DEALER (Independent) - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: NULL"
                    )

            elif user_type == "customer":
                # ✅ NEW DEVICE: Customer creates with parent dealer and distributor
                insert_sql = """
                    INSERT INTO public.device_master
                        (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s)
                    RETURNING device_id;
                """
                cur.execute(insert_sql, (
                    serial_number,
                    parent_dealer_id,
                    parent_distributor_id,
                    user_id,
                    now,
                    now
                ))
                row = cur.fetchone()
                device_id = row[0]
                logger.info(
                    f"✅ Device INSERTED by CUSTOMER - ID: {device_id}, Serial: {serial_number}, Customer: {user_id}, Dealer: {parent_dealer_id}, Dist: {parent_distributor_id}"
                )

        else:
            # ✅ EXISTING DEVICE: UPDATE

            if user_type == "distributor":
                update_sql = """
                    UPDATE public.device_master
                    SET distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (parent_distributor_id, now, device_id))
                logger.info(
                    f"✅ Device UPDATED by DISTRIBUTOR - ID: {device_id}, Serial: {serial_number}, Dist: {parent_distributor_id}"
                )

            elif user_type == "dealer":
                # ✅ UPDATE: Set dealer_id and distributor_id
                update_sql = """
                    UPDATE public.device_master
                    SET dealer_id = %s, distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (user_id, parent_distributor_id, now, device_id))
                logger.info(
                    f"✅ Device UPDATED by DEALER - ID: {device_id}, Serial: {serial_number}, Dealer: {user_id}, Dist: {parent_distributor_id}"
                )

            elif user_type == "customer":
                # ✅ UPDATE: Set customer_id (final assignment)
                update_sql = """
                    UPDATE public.device_master
                    SET customer_id = %s, dealer_id = %s, distributor_id = %s, updated_at = %s
                    WHERE device_id = %s
                    RETURNING device_id;
                """
                cur.execute(update_sql, (user_id, parent_dealer_id, parent_distributor_id, now, device_id))
                logger.info(
                    f"✅ Device UPDATED by CUSTOMER - ID: {device_id}, Serial: {serial_number}, Customer: {user_id}, Dealer: {parent_dealer_id}, Dist: {parent_distributor_id}"
                )

        conn.commit()

        # =====================================================
        # SUCCESS RESPONSE
        # =====================================================
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

@app.route('/api/devices/save-from-qr-v2', methods=['POST'])
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
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        logger.info(f"🔍 {user_type.upper()} {user_id} scanning: {serial_number}")

        parent_distributor_id = None
        parent_dealer_id = None

        # ✅ Distributor/Dealer validation
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

        # =====================================================
        # 🚨 CUSTOMER FLOW
        # =====================================================
        if user_type == "customer":
            # Customer can only scan if device already exists in device_master
            if is_new_device:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "❌ Device not issued by Admin. Contact your Dealer/Distributor first."
                }), 403

            device_id, assigned_dealer_id, assigned_distributor_id, assigned_customer_id = existing_device

            # Device must have dealer or distributor assigned
            if not assigned_dealer_id and not assigned_distributor_id:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "❌ Device not assigned to any Dealer/Distributor. Contact Admin."
                }), 403

            # 🚨 Already assigned to ANY customer (same or different) — BLOCK
            if assigned_customer_id:
                if assigned_customer_id == customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "❌ Device already registered to you."
                    }), 409
                else:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": f"❌ Device already assigned to another Customer."
                    }), 409

            # ✅ Safe to assign to this customer
            cur.execute(
                "UPDATE public.device_master SET customer_id = %s, updated_at = %s WHERE device_id = %s;",
                (customer_id, now, device_id)
            )
            action = "UPDATED"

        # =====================================================
        # 🚨 DISTRIBUTOR FLOW
        # =====================================================
        elif user_type == "distributor":
            if is_new_device:
                # Fresh device — insert with distributor
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

                # 🚨 Already assigned to ANY distributor — BLOCK
                if assigned_distributor_id:
                    if assigned_distributor_id == parent_distributor_id:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "❌ Device already issued to you."
                        }), 409
                    else:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": f"❌ Device already assigned to another Distributor."
                        }), 409

                # 🚨 Already has a dealer assigned — BLOCK
                if assigned_dealer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "❌ Device already assigned to a Dealer."
                    }), 409

                # 🚨 Already has a customer assigned — BLOCK
                if assigned_customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "❌ Device already assigned to a Customer."
                    }), 409

                # ✅ Safe to assign distributor
                cur.execute(
                    "UPDATE public.device_master SET distributor_id = %s, updated_at = %s WHERE device_id = %s;",
                    (parent_distributor_id, now, device_id)
                )
                action = "UPDATED"

        # =====================================================
        # 🚨 DEALER FLOW
        # =====================================================
        elif user_type == "dealer":
            if is_new_device:
                # Fresh device — insert with dealer (+ distributor if linked)
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

                # 🚨 Already assigned to ANY dealer — BLOCK
                if assigned_dealer_id:
                    if assigned_dealer_id == parent_dealer_id:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "❌ Device already issued to you."
                        }), 409
                    else:
                        conn.rollback()
                        return jsonify({
                            "success": False,
                            "message": "❌ Device already assigned to another Dealer."
                        }), 409

                # 🚨 Already has a customer assigned — BLOCK
                if assigned_customer_id:
                    conn.rollback()
                    return jsonify({
                        "success": False,
                        "message": "❌ Device already assigned to a Customer."
                    }), 409

                # ✅ Safe to assign dealer
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
        except:
            pass
        try:
            if conn:
                conn.close()
        except:
            pass
# =========================================================
# UPDATED DEVICE SAVE ENDPOINT (with validation feedback)
# =========================================================
# @app.route('/api/devices/save-from-qr', methods=['POST'])
# def save_device_qr():
#     """
#     Expected JSON payload:
#     {
#         "serial_number": "ABC123XYZ789",
#         "user_id": 5,
#         "user_type": "dealer"  (or "distributor" or "customer")
#         "qr_data": "raw QR code data (optional)"
#     }
#     """
#     conn = None
#     cur = None

#     try:
#         # Validate request content type
#         if not request.is_json:
#             return jsonify({
#                 "success": False,
#                 "message": "Content-Type must be application/json",
#                 "error_code": "INVALID_CONTENT_TYPE"
#             }), 400

#         data = request.get_json(silent=True) or {}

#         # ==================== VALIDATION ====================
#         if not data.get("serial_number"):
#             return jsonify({
#                 "success": False,
#                 "message": "serial_number is required",
#                 "error_code": "MISSING_SERIAL_NUMBER"
#             }), 400

#         if not data.get("user_id"):
#             return jsonify({
#                 "success": False,
#                 "message": "user_id is required",
#                 "error_code": "MISSING_USER_ID"
#             }), 400

#         if not data.get("user_type"):
#             return jsonify({
#                 "success": False,
#                 "message": "user_type is required (dealer, distributor, or customer)",
#                 "error_code": "MISSING_USER_TYPE"
#             }), 400

#         serial_number = str(data.get("serial_number", "")).strip().upper()
#         user_id = int(data.get("user_id"))
#         user_type = str(data.get("user_type", "")).strip().lower()
#         qr_data = str(data.get("qr_data", "")).strip()

#         if len(serial_number) < 3:
#             return jsonify({
#                 "success": False,
#                 "message": "serial_number must be at least 3 characters",
#                 "error_code": "INVALID_SERIAL_NUMBER"
#             }), 400

#         valid_user_types = ["dealer", "distributor", "customer"]
#         if user_type not in valid_user_types:
#             return jsonify({
#                 "success": False,
#                 "message": f"user_type must be one of: {', '.join(valid_user_types)}",
#                 "error_code": "INVALID_USER_TYPE"
#             }), 400

#         # Map user_id into correct column
#         dealer_id = user_id if user_type == "dealer" else None
#         distributor_id = user_id if user_type == "distributor" else None
#         customer_id = user_id if user_type == "customer" else None

#         now = datetime.now(timezone.utc)

#         conn = psycopg2.connect(**DB_CONFIG)
#         cur = conn.cursor()

#         # =====================================================
#         # Check if serial exists in system_information table
#         # (Additional validation before saving)
#         # =====================================================
#         check_sql = """
#             SELECT device_id FROM public.system_information
#             WHERE UPPER(serial_number) = %s
#             LIMIT 1;
#         """
#         cur.execute(check_sql, (serial_number,))
#         sys_info = cur.fetchone()

#         if sys_info is None:
#             conn.rollback()
#             return jsonify({
#                 "success": False,
#                 "message": f'Serial number "{serial_number}" does not exist in system information',
#                 "error_code": "SERIAL_NOT_IN_SYSTEM_INFO"
#             }), 400

#         # =====================================================
#         # ✅ BEST + ROBUST DUPLICATE HANDLING (RACE-SAFE)
#         # Requires UNIQUE constraint on serial_number.
#         # =====================================================
#         insert_sql = """
#             INSERT INTO public.device_master
#                 (serial_number, dealer_id, distributor_id, customer_id, created_at, updated_at)
#             VALUES
#                 (%s, %s, %s, %s, %s, %s)
#             ON CONFLICT (serial_number) DO NOTHING
#             RETURNING device_id;
#         """

#         cur.execute(insert_sql, (
#             serial_number,
#             dealer_id,
#             distributor_id,
#             customer_id,
#             now,
#             now
#         ))

#         row = cur.fetchone()

#         # If conflict happened, row will be None -> fetch existing id
#         if row is None:
#             cur.execute(
#                 "SELECT device_id FROM public.device_master WHERE serial_number = %s LIMIT 1",
#                 (serial_number,)
#             )
#             existing = cur.fetchone()
#             conn.rollback()

#             return jsonify({
#                 "success": False,
#                 "message": f'Device with serial number "{serial_number}" already exists',
#                 "error_code": "DUPLICATE_DEVICE",
#                 "device_id": existing[0] if existing else None
#             }), 409

#         device_id = row[0]
#         conn.commit()

#         logger.info(
#             f"Device saved successfully - Device ID: {device_id}, Serial: {serial_number}, "
#             f"Type: {user_type}, User ID: {user_id}"
#         )

#         # Determine success message based on user type
#         user_type_label = "DEALER" if user_type == "dealer" else "DISTRIBUTOR" if user_type == "distributor" else "CUSTOMER"

#         return jsonify({
#             "success": True,
#             "message": f"ISSUED TO {user_type_label} SUCCESSFULLY",
#             "device_id": device_id,
#             "user_id": user_id,
#             "user_type": user_type,
#             "serial_number": serial_number,
#             "qr_data": qr_data,  # keeping it in response (not DB)
#             "timestamp": now.isoformat()
#         }), 201

#     except ValueError as ve:
#         if conn:
#             conn.rollback()
#         logger.error(f"ValueError in save_device_qr: {str(ve)}", exc_info=True)
#         return jsonify({
#             "success": False,
#             "message": f"Invalid data format: {str(ve)}",
#             "error_code": "INVALID_DATA_FORMAT"
#         }), 400

#     except Exception as e:
#         if conn:
#             conn.rollback()
#         logger.error(f"Error saving device from QR: {str(e)}", exc_info=True)
#         return jsonify({
#             "success": False,
#             "message": f"Failed to save device: {str(e)}",
#             "error_code": "DATABASE_ERROR"
#         }), 500

#     finally:
#         try:
#             if cur:
#                 cur.close()
#         except Exception:
#             pass
#         try:
#             if conn:
#                 conn.close()
#         except Exception:
#             pass
    
# SMS API Configuration
SMS_API_CONFIG = {
    'username': 'videolytical.trans',
    'password': 'W9gnP',
    'from': 'VIDESY',
    'dltPrincipalEntityId': '1701174340505126708',
    'dltContentId': '1707175170253644044'
}

OTP_VALID_MINUTES = 5

def generate_otp(length: int = 4) -> str:
    return "".join(random.choice("0123456789") for _ in range(length))


@app.route("/send-otp", methods=["POST"])
def send_otp():
    conn = None
    try:
        json_data = request.get_json(silent=True) or {}
        phone = (request.form.get("phone") or json_data.get("phone") or "").strip()

        if not phone:
            return jsonify({
                "status": "error",
                "message": "Phone number is required",
                "errors": {"detail": "phone field is missing"}
            }), 400

        digits_only = "".join(ch for ch in phone if ch.isdigit())
        if len(digits_only) < 10:
            return jsonify({
                "status": "error",
                "message": "Please enter a valid phone number",
                "errors": {"detail": "phone is invalid"}
            }), 400

        phone_to_send = digits_only

        # ===== CHECK IF PHONE NUMBER IS ALREADY REGISTERED =====
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM user_signups WHERE phone_number = %s LIMIT 1
            """, (phone_to_send,))
            if cursor.fetchone():
                return jsonify({
                    "status": "error",
                    "message": "Phone number is already registered.",
                    "errors": {"detail": "This phone number is already in use."}
                }), 400

        # ✅ Generate OTP + expiry
        otp = generate_otp(4)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_VALID_MINUTES)

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO otp_verification (phone_number, otp, expires_at, is_verified, attempts, created_at, updated_at)
                VALUES (%s, %s, %s, FALSE, 0, NOW(), NOW())
                ON CONFLICT (phone_number)
                DO UPDATE SET
                    otp = EXCLUDED.otp,
                    expires_at = EXCLUDED.expires_at,
                    is_verified = FALSE,
                    attempts = 0,
                    updated_at = NOW()
            """, (digits_only, otp, expires_at))

            conn.commit()

        # Sending OTP SMS logic remains the same
        message = (
            f"Dear Customer, Use this One-Time Password {otp} to log in to your account. "
            f"This OTP will be valid for the next {OTP_VALID_MINUTES} mins. Team Videolytical Systems"
        )

        base_url = "https://pgapi.smartping.ai/fe/api/v1/send"
        params = {
            "username": "videolytical.trans",
            "password": "W9gnP",
            "unicode": "false",
            "from": "VIDESY",
            "to": phone_to_send,
            "dltPrincipalEntityId": "1701174340505126708",
            "dltContentId": "1707175170253644044",
            "text": message,
        }

        try:
            response = requests.get(base_url, params=params, timeout=15)
        except requests.RequestException as re:
            return jsonify({
                "status": "error",
                "message": "SMS API request failed.",
                "errors": {"detail": str(re)}
            }), 502

        if response.status_code != 200 or "success" not in (response.text or "").lower():
            return jsonify({
                "status": "error",
                "message": "Failed to send OTP SMS.",
                "errors": {"detail": f"Failed to send OTP SMS: {response.text}"}
            }), 500

        return jsonify({
            "status": "success",
            "message": f"OTP sent successfully to {phone_to_send}",
            "data": {
                "phone_no": phone_to_send,
                "expires_in_minutes": OTP_VALID_MINUTES
            }
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    conn = None
    cursor = None
    try:
        json_data = request.get_json(silent=True) or {}

        phone = (request.form.get("phone") or json_data.get("phone") or "").strip()
        otp = (request.form.get("otp") or json_data.get("otp") or "").strip()

        if not phone or not otp:
            return jsonify({
                "status": "error",
                "message": "Phone and OTP are required",
                "errors": {"detail": "phone/otp missing"}
            }), 400

        digits_only = "".join(ch for ch in phone if ch.isdigit())
        if len(digits_only) < 10:
            return jsonify({
                "status": "error",
                "message": "Invalid phone number",
                "errors": {"detail": "phone invalid"}
            }), 400

        if not (otp.isdigit() and len(otp) == 4):
            return jsonify({
                "status": "error",
                "message": "OTP must be exactly 4 digits.",
                "errors": {"detail": "otp invalid"}
            }), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                phone_number,
                otp,
                expires_at,
                is_verified,
                COALESCE(attempts, 0) AS attempts,
                (expires_at < NOW()) AS expired
            FROM otp_verification
            WHERE phone_number = %s
        """, (digits_only,))
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "status": "error",
                "message": "OTP record not found for this phone number.",
                "errors": {"detail": "otp record not found"}
            }), 404

        if row["expired"]:
            return jsonify({
                "status": "error",
                "message": "OTP has expired. Please request a new OTP.",
                "errors": {"detail": "otp expired"}
            }), 400

        if row.get("is_verified"):
            return jsonify({
                "status": "success",
                "message": "OTP already verified.",
                "data": {"phone_no": digits_only, "verified": True}
            }), 200

        if str(row["otp"]) != str(otp):
            cursor.execute("""
                UPDATE otp_verification
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = NOW()
                WHERE phone_number = %s
            """, (digits_only,))
            conn.commit()

            return jsonify({
                "status": "error",
                "message": "Invalid OTP.",
                "errors": {"detail": "invalid otp"}
            }), 400

        cursor.execute("""
            UPDATE otp_verification
            SET is_verified = TRUE,
                attempts = 0,
                updated_at = NOW()
            WHERE phone_number = %s
        """, (digits_only,))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "OTP verified successfully.",
            "data": {"phone_no": digits_only, "verified": True}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        print("---- /verify-otp ERROR ----")
        traceback.print_exc()
        print("---------------------------")

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ================================
# SMTP CONFIG (PGAK / Custom SMTP)
# ================================
def _bool_env(val: str, default: bool = True) -> bool:
    if val is None:
        return default
    v = str(val).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

SMTP_CONFIG = {
    "host": os.getenv("SMTP_HOST", "").strip(),
    "port": int(os.getenv("SMTP_PORT", "587")),
    "username": os.getenv("SMTP_USERNAME", "").strip(),
    "password": os.getenv("SMTP_PASSWORD", "").strip(),
    "from_email": (os.getenv("SMTP_FROM_EMAIL", "").strip() or os.getenv("SMTP_USERNAME", "").strip()),
    "use_tls": _bool_env("SMTP_USE_TLS", True),
    "debug": _bool_env("SMTP_DEBUG", False),

    # ✅ This fixes hostname mismatch when SMTP_HOST is an alias
    "tls_server_name": os.getenv("SMTP_TLS_SERVER_NAME", "").strip(),

    # Last-resort: not recommended
    "skip_hostname_verify": _bool_env("SMTP_TLS_SKIP_HOSTNAME_VERIFY", False),
}

def _smtp_starttls_with_server_name(server: smtplib.SMTP, context: ssl.SSLContext, server_name: str):
    code, resp = server.docmd("STARTTLS")
    if code != 220:
        raise smtplib.SMTPResponseException(code, resp)

    # ✅ Wrap with SNI + hostname verification against server_name
    server.sock = context.wrap_socket(server.sock, server_hostname=server_name)
    server.file = server.sock.makefile("rb")

    # Reset SMTP state after TLS
    server.helo_resp = None
    server.ehlo_resp = None
    server.esmtp_features = {}
    server.does_esmtp = False
    server.ehlo()

def send_email_otp(to_email: str, otp: str, otp_valid_minutes: int = 5) -> bool:
    try:
        if not SMTP_CONFIG["host"] or not SMTP_CONFIG["username"] or not SMTP_CONFIG["password"]:
            raise ValueError("Missing SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD in .env")

        subject = "Your OTP for Pgak Dealer Portal Verification"
        html_message = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
          <div style="background-color:#f5f5f5; padding:20px; border-radius:10px;">
            <h2 style="color:#58a6ff;">Email Verification</h2>
            <p>Your One-Time Password (OTP) is:</p>
            <h1 style="color:#00aa66; letter-spacing:5px; font-size:2em;">{otp}</h1>
            <p style="color:#8b949e;">This OTP will expire in {otp_valid_minutes} minutes.</p>
            <p style="color:#8b949e; font-size:0.9em;">Team Videolytical Systems</p>
          </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_CONFIG["from_email"]
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_message, "html"))

        host = SMTP_CONFIG["host"]
        port = SMTP_CONFIG["port"]

        context = ssl.create_default_context()

        # ⚠️ Not recommended, only for testing
        if SMTP_CONFIG["skip_hostname_verify"]:
            context.check_hostname = False

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as server:
                if SMTP_CONFIG["debug"]:
                    server.set_debuglevel(1)
                server.ehlo()
                server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as server:
                if SMTP_CONFIG["debug"]:
                    server.set_debuglevel(1)

                server.ehlo()

                if SMTP_CONFIG["use_tls"]:
                    # ✅ If tls_server_name is set, verify cert against that name
                    if SMTP_CONFIG["tls_server_name"] and not SMTP_CONFIG["skip_hostname_verify"]:
                        _smtp_starttls_with_server_name(server, context, SMTP_CONFIG["tls_server_name"])
                    else:
                        server.starttls(context=context)
                        server.ehlo()

                server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
                server.send_message(msg)

        return True

    except Exception as e:
        print(f"Error sending email OTP: {str(e)}")
        traceback.print_exc()
        return False

if not SMTP_CONFIG["from_email"]:
    SMTP_CONFIG["from_email"] = SMTP_CONFIG["username"]

    

# ================================
# HELPERS
# ================================
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

def is_valid_email(email: str) -> bool:
    return bool(email and EMAIL_REGEX.match(email))




# ================================
# SEND OTP ENDPOINT
# ================================
@app.route("/send-otp-email", methods=["POST"])
def send_otp_email():
    conn = None
    try:
        json_data = request.get_json(silent=True) or {}

        email = (request.form.get("email") or json_data.get("email") or "").strip().lower()
        if not email:
            return jsonify({
                "status": "error",
                "message": "Email is required",
                "errors": {"detail": "email field is missing"}
            }), 400

        if not is_valid_email(email):
            return jsonify({
                "status": "error",
                "message": "Please enter a valid email",
                "errors": {"detail": "email is invalid"}
            }), 400

        # ===== CHECK IF EMAIL IS ALREADY REGISTERED =====
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM user_signups WHERE email = %s LIMIT 1
            """, (email,))
            if cursor.fetchone():
                return jsonify({
                    "status": "error",
                    "message": "Email is already registered.",
                    "errors": {"detail": "This email is already in use."}
                }), 400

        otp = generate_otp(4)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_VALID_MINUTES)

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO otp_verification_email (email, otp, expires_at, is_verified, attempts, created_at, updated_at)
                VALUES (%s, %s, %s, FALSE, 0, NOW(), NOW())
                ON CONFLICT (email)
                DO UPDATE SET
                    otp = EXCLUDED.otp,
                    expires_at = EXCLUDED.expires_at,
                    is_verified = FALSE,
                    attempts = 0,
                    updated_at = NOW()
            """, (email, otp, expires_at))
            conn.commit()

        ok = send_email_otp(email, otp)
        if not ok:
            return jsonify({
                "status": "error",
                "message": "Failed to send OTP email.",
                "errors": {"detail": "SMTP login/send failed. Check SMTP credentials and server settings."}
            }), 502

        return jsonify({
            "status": "success",
            "message": f"OTP sent successfully to {email}",
            "data": {"email": email, "expires_in_minutes": OTP_VALID_MINUTES}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

# ================================
# VERIFY OTP ENDPOINT (same as yours)
# ================================
@app.route("/verify-otp-email", methods=["POST"])
def verify_otp_email():
    conn = None
    cursor = None
    try:
        json_data = request.get_json(silent=True) or {}

        email = (request.form.get("email") or json_data.get("email") or "").strip().lower()
        otp = (request.form.get("otp") or json_data.get("otp") or "").strip()

        if not email or not otp:
            return jsonify({
                "status": "error",
                "message": "Email and OTP are required",
                "errors": {"detail": "email/otp missing"}
            }), 400

        if not is_valid_email(email):
            return jsonify({
                "status": "error",
                "message": "Invalid email",
                "errors": {"detail": "email invalid"}
            }), 400

        if not (otp.isdigit() and len(otp) == 4):
            return jsonify({
                "status": "error",
                "message": "OTP must be exactly 4 digits.",
                "errors": {"detail": "otp invalid"}
            }), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                email,
                otp,
                expires_at,
                is_verified,
                attempts,
                (expires_at < NOW()) AS expired
            FROM otp_verification_email
            WHERE email = %s
        """, (email,))
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "status": "error",
                "message": "OTP record not found for this email.",
                "errors": {"detail": "otp record not found"}
            }), 404

        if row["expired"]:
            return jsonify({
                "status": "error",
                "message": "OTP has expired. Please request a new OTP.",
                "errors": {"detail": "otp expired"}
            }), 400

        if row.get("is_verified"):
            return jsonify({
                "status": "success",
                "message": "OTP already verified.",
                "data": {"email": email, "verified": True}
            }), 200

        if str(row["otp"]) != str(otp):
            cursor.execute("""
                UPDATE otp_verification_email
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = NOW()
                WHERE email = %s
            """, (email,))
            conn.commit()

            return jsonify({
                "status": "error",
                "message": "Invalid OTP.",
                "errors": {"detail": "invalid otp"}
            }), 400

        cursor.execute("""
            UPDATE otp_verification_email
            SET is_verified = TRUE,
                attempts = 0,
                updated_at = NOW()
            WHERE email = %s
        """, (email,))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "OTP verified successfully.",
            "data": {"email": email, "verified": True}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        print("---- /verify-otp-email ERROR ----")
        traceback.print_exc()
        print("--------------------------------")

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

@app.route("/admin/users", methods=["GET"])
def admin_users():
    """
    Fetch all users with their details (dealers, distributors, admins)
    Returns JSON with user_id as key and user details as value

    🎯 FIX: Excludes the currently logged-in admin from the list
    🎯 NEW: Also returns distributor's name/details for the distributor_code (if any)
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        current_user_id = session.get('user_id')

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ✅ LATERAL join ensures ONLY ONE distributor row is picked even if duplicates exist
        cursor.execute("""
            SELECT
                u.user_id,
                u.full_name,
                u.address,
                u.email,
                u.phone_number,
                u.user_type,
                u.status,
                u.gst_no,
                u.company_name,
                u.pincode,
                u.distributor_code,
                u.dealer_code,
                u.created_at,

                d.distributor_user_id,
                d.distributor_full_name,
                d.distributor_email,
                d.distributor_phone_number

            FROM user_signups u
            LEFT JOIN LATERAL (
                SELECT
                    us.user_id AS distributor_user_id,
                    us.full_name AS distributor_full_name,
                    us.email AS distributor_email,
                    us.phone_number AS distributor_phone_number
                FROM user_signups us
                WHERE us.user_type = 'distributor'
                  AND us.distributor_code = u.distributor_code
                ORDER BY us.created_at DESC NULLS LAST
                LIMIT 1
            ) d ON TRUE
            WHERE u.user_id != %s
            ORDER BY u.created_at DESC
        """, (current_user_id,))

        users = cursor.fetchall()
        cursor.close()
        conn.close()

        users_dict = {}
        for user in users:
            users_dict[str(user['user_id'])] = {
                "user_id": user.get('user_id'),
                "full_name": user.get('full_name'),
                "address": user.get('address'),
                "email": user.get('email'),
                "phone_number": user.get('phone_number'),
                "user_type": user.get('user_type'),
                "status": user.get('status'),
                "gst_no": user.get('gst_no'),
                "company_name": user.get('company_name'),
                "pincode": user.get('pincode'),
                "distributor_code": user.get('distributor_code'),
                "dealer_code": user.get('dealer_code'),
                "created_at": user['created_at'].isoformat() if user.get('created_at') else None,

                # ✅ NEW FIELDS (distributor details for that distributor_code)
                "distributor_user_id": user.get('distributor_user_id'),
                "distributor_full_name": user.get('distributor_full_name'),
                "distributor_email": user.get('distributor_email'),
                "distributor_phone_number": user.get('distributor_phone_number'),
            }

        return jsonify(users_dict), 200

    except Exception as e:
        print("---- /admin/users ERROR ----")
        traceback.print_exc()
        print("---------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch users",
            "error": str(e)
        }), 500


@app.route("/admin/edit-users/<int:user_id>", methods=["PUT"])
def edit_user(user_id):
    """
    Edit an existing user's details
    ✅ FIX: status was required but was NOT being updated earlier
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}

        required_fields = ['full_name', 'email', 'address', 'phone_number', 'user_type', 'status', 'company_name']
        for field in required_fields:
            if field not in data or not str(data[field]).strip():
                return jsonify({"status": "error", "message": f"{field} is required"}), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT user_id FROM user_signups
            WHERE email = %s AND user_id != %s
        """, (data['email'], user_id))

        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Email already exists"}), 400

        cursor.execute("""
            UPDATE user_signups
            SET
                full_name = %s,
                address = %s,
                email = %s,
                phone_number = %s,
                user_type = %s,
                status = %s,
                gst_no = %s,
                company_name = %s,
                pincode = %s,
                distributor_code = %s,
                dealer_code = %s
            WHERE user_id = %s
        """, (
            data['full_name'],
            data['email'],
            data['address'],
            data['phone_number'],
            data['user_type'],
            data['status'],  # ✅ NEW: status update fix
            data.get('gst_no', ''),
            data['company_name'],
            data.get('pincode', ''),
            data.get('distributor_code', ''),
            data.get('dealer_code', ''),
            user_id
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "User updated successfully"
        }), 200

    except Exception as e:
        print("---- /admin/edit-users/<user_id> PUT ERROR ----")
        traceback.print_exc()
        print("----------------------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to update user",
            "error": str(e)
        }), 500


@app.route("/admin/delete-users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    """
    Delete a user
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        if user_id == session.get('user_id'):
            return jsonify({"status": "error", "message": "Cannot delete your own account"}), 400

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM user_signups WHERE user_id = %s", (user_id,))

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "User not found"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "User deleted successfully"
        }), 200

    except Exception as e:
        print("---- /admin/delete-users/<user_id> DELETE ERROR ----")
        traceback.print_exc()
        print("---------------------------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to delete user",
            "error": str(e)
        }), 500
    
# @app.route("/admin/post-users", methods=["POST"])
# def create_user():
#     """
#     Create a new user (dealer or distributor)
#     Admin-created users are auto-approved
#     """
#     try:
#         # ===== AUTHENTICATION CHECK =====
#         if 'user_type' not in session or session['user_type'] != 'admin':
#             return jsonify({"status": "error", "message": "Unauthorized"}), 401

#         data = request.get_json()
        
#         if not data:
#             return jsonify({"status": "error", "message": "No data provided"}), 400

#         # ===== EXTRACT FORM DATA =====
#         user_type = data.get("user_type", "").lower().strip()
#         full_name = data.get("full_name", "").strip()
#         phone_number = data.get("phone_number", "").strip()
#         email = data.get("email", "").strip()
#         password = data.get("password", "")
#         confirm_password = data.get("confirm_password", "")
#         company_name = data.get("company_name", "").strip()
#         gst_no = data.get("gst_no", "").strip()
#         pincode = data.get("pincode", "").strip()

#         # ===== VALIDATION: Required Fields =====
#         required_fields = {
#             'full_name': full_name,
#             'email': email,
#             'phone_number': phone_number,
#             'user_type': user_type,
#             'company_name': company_name,
#             'gst_no': gst_no,
#             'pincode': pincode,
#             'password': password,
#             'confirm_password': confirm_password
#         }

#         # ===== VALIDATION: User Type =====
#         if user_type not in ['dealer', 'distributor']:
#             return jsonify({
#                 "status": "error",
#                 "message": "User Type must be 'dealer' or 'distributor'"
#             }), 400

#         # ===== VALIDATION: Password Match =====
#         if password != confirm_password:
#             return jsonify({
#                 "status": "error",
#                 "message": "Passwords do not match"
#             }), 400

#         # ===== VALIDATION: Password Length =====
#         if len(password) < 8:
#             return jsonify({
#                 "status": "error",
#                 "message": "Password must be at least 8 characters long"
#             }), 400

#         # ===== VALIDATION: Email Format =====
#         email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
#         if not re.match(email_regex, email):
#             return jsonify({
#                 "status": "error",
#                 "message": "Invalid email format"
#             }), 400
        
#         # ===== HASH PASSWORD =====
#         try:
#             hashed_password = pwd_context.hash(password)
#         except Exception as e:
#             print(f"Password hashing error: {str(e)}")
#             return jsonify({
#                 "status": "error",
#                 "message": "Error processing password"
#             }), 500

#         conn = None
#         try:
#             conn = psycopg2.connect(**DB_CONFIG)
#             cursor = conn.cursor()

#             # ===== DUPLICATE CHECK: Email =====
#             cursor.execute(
#                 "SELECT user_id FROM user_signups WHERE LOWER(email) = LOWER(%s)",
#                 (email,)
#             )
#             if cursor.fetchone():
#                 cursor.close()
#                 conn.close()
#                 return jsonify({
#                     "status": "error",
#                     "message": "Email already registered"
#                 }), 400

#             # ===== DUPLICATE CHECK: Phone Number =====
#             cursor.execute(
#                 "SELECT user_id FROM user_signups WHERE phone_number = %s",
#                 (phone_number,)
#             )
#             if cursor.fetchone():
#                 cursor.close()
#                 conn.close()
#                 return jsonify({
#                     "status": "error",
#                     "message": "Phone number already registered"
#                 }), 400

#             # ===== GENERATE CODES BASED ON USER TYPE =====
#             dealer_code = ''
#             distributor_code = ''

#             if user_type == 'dealer':
#                 # Generate unique dealer code
#                 dealer_code = get_unique_code('dealer', cursor)

#             elif user_type == 'distributor':
#                 # Generate unique distributor code
#                 distributor_code = get_unique_code('distributor', cursor)

#             # ===== INSERT NEW USER =====
#             cursor.execute(""" 
#                 INSERT INTO user_signups 
#                 (full_name, email, phone_number, password, user_type, status, 
#                  gst_no, company_name, pincode, distributor_code, dealer_code, created_at)
#                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
#                 RETURNING user_id, full_name, email, user_type, dealer_code, distributor_code
#             """, (
#                 full_name,
#                 email,
#                 phone_number,
#                 hashed_password,
#                 user_type,
#                 'Approved',  # Admin-created users are auto-approved
#                 gst_no,
#                 company_name,
#                 pincode,
#                 distributor_code,
#                 dealer_code
#             ))

#             result = cursor.fetchone()
            
#             if not result:
#                 conn.rollback()
#                 cursor.close()
#                 conn.close()
#                 return jsonify({
#                     "status": "error",
#                     "message": "Failed to create user"
#                 }), 500
            
#             new_user_id = result[0]
#             conn.commit()
#             cursor.close()

#             # ===== SUCCESS RESPONSE =====
#             return jsonify({
#                 "status": "success",
#                 "message": f"{user_type.capitalize()} created successfully",
#                 "user_id": new_user_id,
#                 "user": {
#                     "user_id": result[0],
#                     "full_name": result[1],
#                     "email": result[2],
#                     "user_type": result[3],
#                     "dealer_code": result[4],
#                     "distributor_code": result[5],
#                     "status": "approved"
#                 }
#             }), 201

#         except psycopg2.IntegrityError as e:
#             if conn:
#                 conn.rollback()
#                 cursor.close()
#                 conn.close()

#             print("---- DATABASE INTEGRITY ERROR ----")
#             print(str(e))
#             print("----------------------------------")
            
#             # Check for specific constraint violations
#             error_msg = str(e).lower()
#             if 'email' in error_msg:
#                 return jsonify({
#                     "status": "error",
#                     "message": "Email already registered"
#                 }), 400
#             elif 'phone' in error_msg:
#                 return jsonify({
#                     "status": "error",
#                     "message": "Phone number already registered"
#                 }), 400

#             return jsonify({
#                 "status": "error",
#                 "message": "Database integrity error. Please check your input."
#             }), 400

#         except psycopg2.DatabaseError as e:
#             if conn:
#                 conn.rollback()
#                 cursor.close()
#                 conn.close()

#             print("---- DATABASE ERROR ----")
#             print(str(e))
#             print("------------------------")

#             return jsonify({
#                 "status": "error",
#                 "message": "Database error occurred"
#             }), 500
        
#         except Exception as e:
#             if conn:
#                 conn.rollback()
#                 cursor.close()
#                 conn.close()
            
#             print("---- UNEXPECTED ERROR ----")
#             print(str(e))
#             print("--------------------------")
            
#             return jsonify({
#                 "status": "error",
#                 "message": "An unexpected error occurred"
#             }), 500

#         finally:
#             if conn and not conn.closed:
#                 try:
#                     cursor.close()
#                     conn.close()
#                 except:
#                     pass

#     except Exception as e:
#         print("---- OUTER EXCEPTION ----")
#         print(str(e))
#         print("-----------------------")
#         return jsonify({
#             "status": "error",
#             "message": "An unexpected error occurred"
#         }), 500


@app.route("/admin/post-users", methods=["POST"])
def create_user():
    """
    Create a new user (dealer or distributor)
    Admin-created users are auto-approved
    """
    try:
        # ===== AUTHENTICATION CHECK =====
        print("Checking user authentication...")
        if 'user_type' not in session or session['user_type'] != 'admin':
            print("User is not an admin")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json()
        print(f"Received data: {data}")

        # ===== EXTRACT FORM DATA =====
        user_type = data.get("user_type", "").lower()  # 'dealer' or 'distributor'
        full_name = data.get("full_name", "").strip()
        address = data.get("address", "").strip()
        phone_number = data.get("phone_number", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "")
        confirm_password = data.get("confirm_password", "")

        print(f"user_type: {user_type}, full_name: {full_name}, email: {email}, phone_number: {phone_number}")

        # ===== VALIDATION: Required Fields =====
        required_fields = {
            'full_name': full_name,
            'address': address,
            'email': email,
            'phone_number': phone_number,
            'user_type': user_type,
            'company_name': data.get('company_name', '').strip(),
            'password': password,
            'confirm_password': confirm_password
        }

        for field_name, field_value in required_fields.items():
            if not field_value:
                print(f"Missing required field: {field_name}")
                return jsonify({
                    "status": "error",
                    "message": f"{field_name.replace('_', ' ').title()} is required"
                }), 400

        # ===== VALIDATION: User Type =====
        if user_type not in ['dealer', 'distributor']:
            print(f"Invalid user_type: {user_type}")
            return jsonify({
                "status": "error",
                "message": "user_type must be 'dealer' or 'distributor'"
            }), 400

        # ===== VALIDATION: Password Match =====
        if password != confirm_password:
            print("Passwords do not match")
            return jsonify({
                "status": "error",
                "message": "Passwords do not match"
            }), 400

        # ===== VALIDATION: Password Length =====
        if len(password) < 8:
            print("Password length is less than 8 characters")
            return jsonify({
                "status": "error",
                "message": "Password must be at least 8 characters long"
            }), 400

        # ===== VALIDATION: Email Format =====
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            print(f"Invalid email format: {email}")
            return jsonify({
                "status": "error",
                "message": "Invalid email format"
            }), 400

        # ===== VALIDATION: Phone Number Format =====
        phone_regex = r'^\d{7,15}$'
        if not re.match(phone_regex, phone_number.replace(' ', '').replace('-', '')):
            print(f"Invalid phone number format: {phone_number}")
            return jsonify({
                "status": "error",
                "message": "Phone number must be 7-15 digits"
            }), 400

        # ===== EXTRACT DEALER/DISTRIBUTOR SPECIFIC FIELDS =====
        gst_no = data.get("gst_no", "").strip()
        company_name = data.get("company_name", "").strip()
        pincode = data.get("pincode", "").strip()

        print(f"gst_no: {gst_no}, company_name: {company_name}, pincode: {pincode}")

        # ===== VALIDATION: Dealer/Distributor Required Fields =====
        if not company_name:
            print("Company Name is required")
            return jsonify({
                "status": "error",
                "message": "Company Name is required"
            }), 400

        if not gst_no:
            print("GST Number is required")
            return jsonify({
                "status": "error",
                "message": "GST Number is required"
            }), 400

        if not pincode:
            print("Pincode is required")
            return jsonify({
                "status": "error",
                "message": "Pincode is required"
            }), 400

        # ===== VALIDATION: Pincode Format =====
        if not re.match(r'^\d{5,10}$', pincode):
            print(f"Invalid pincode format: {pincode}")
            return jsonify({
                "status": "error",
                "message": "Pincode must be 5-10 digits"
            }), 400

        # ===== HASH PASSWORD =====
        try:
            print("Hashing password...")
            hashed_password = pwd_context.hash(password)
        except Exception as e:
            print(f"Error hashing password: {e}")
            return jsonify({
                "status": "error",
                "message": "Error processing password"
            }), 500

        conn = None
        try:
            print("Connecting to database...")
            conn = psycopg2.connect(**DB_CONFIG)
            cursor = conn.cursor()

            # ===== DUPLICATE CHECK: Email =====
            cursor.execute(
                "SELECT user_id FROM user_signups WHERE LOWER(email) = LOWER(%s)",
                (email,)
            )
            if cursor.fetchone():
                print(f"Email already registered: {email}")
                cursor.close()
                conn.close()
                return jsonify({
                    "status": "error",
                    "message": "Email already registered"
                }), 400

            # ===== DUPLICATE CHECK: Phone Number =====
            cursor.execute(
                "SELECT user_id FROM user_signups WHERE phone_number = %s",
                (phone_number,)
            )
            if cursor.fetchone():
                print(f"Phone number already registered: {phone_number}")
                cursor.close()
                conn.close()
                return jsonify({
                    "status": "error",
                    "message": "Phone number already registered"
                }), 400

            # ===== GENERATE CODES BASED ON USER TYPE =====
            # ===== GENERATE CODES BASED ON USER TYPE =====
            dealer_code = None  # Set default as None
            distributor_code = ''

            if user_type == 'dealer':
                # Generate unique dealer code
                dealer_code = get_unique_code('dealer', cursor)

            elif user_type == 'distributor':
                # Generate unique distributor code
                distributor_code = get_unique_code('distributor', cursor)

            print(f"Generated dealer_code: {dealer_code}, distributor_code: {distributor_code}")

            # ===== INSERT NEW USER =====
            cursor.execute(""" 
                INSERT INTO user_signups 
                (full_name, address, email, phone_number, password, user_type, status, 
                gst_no, company_name, pincode, distributor_code, dealer_code, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING user_id, full_name, address, email, user_type, dealer_code, distributor_code
            """, (
                full_name,
                address,
                email,
                phone_number,
                hashed_password,
                user_type,
                'Approved',  # Admin-created users are auto-approved
                gst_no,
                company_name,
                pincode,
                distributor_code,
                dealer_code
            ))

            result = cursor.fetchone()
            new_user_id = result[0]

            print(f"New user created with user_id: {new_user_id}")
            
            conn.commit()
            cursor.close()

            # ===== SUCCESS RESPONSE =====
            return jsonify({
                "status": "success",
                "message": f"{user_type.capitalize()} created successfully",
                "user_id": new_user_id,
                "user": {
                    "user_id": result[0],
                    "full_name": result[1],
                    "address": result[2],
                    "email": result[3],
                    "user_type": result[4],
                    "dealer_code": result[5],
                    "distributor_code": result[6],
                    "status": "approved"
                }
            }), 201

        except psycopg2.IntegrityError as e:
            if conn:
                conn.rollback()
                cursor.close()
                conn.close()

            print("---- DATABASE INTEGRITY ERROR ----")
            print(str(e))
            print("----------------------------------")

            return jsonify({
                "status": "error",
                "message": "Database integrity error. Please check your input."
            }), 400

        except psycopg2.DatabaseError as e:
            if conn:
                conn.rollback()
                cursor.close()
                conn.close()

            print("---- DATABASE ERROR ----")
            print(str(e))
            print("------------------------")

            return jsonify({
                "status": "error",
                "message": "Database error occurred"
            }), 500

        finally:
            if conn and not conn.closed:
                cursor.close()
                conn.close()

    except Exception as e:
        print("---- /admin/users POST ERROR ----")
        traceback.print_exc()
        print("---------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to create user",
            "error": str(e) if app.debug else "Internal server error"
        }), 500


@app.route("/admin/pending", methods=["GET"])
def admin_pending():
    """
    Fetch only pending requests (dealers and distributors awaiting approval)
    Returns JSON with user_id as key and user details as value
    
    🎯 FIX: Excludes the currently logged-in admin from the list
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        # 🎯 Get the logged-in user's ID
        current_user_id = session.get('user_id')

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT 
                user_id,
                full_name,
                address,
                email,
                phone_number,
                user_type,
                status,
                gst_no,
                company_name,
                pincode,
                distributor_code,
                dealer_code,
                created_at
            FROM user_signups
            WHERE status = 'Pending' AND (user_type = 'dealer' OR user_type = 'distributor')
            AND user_id != %s
            ORDER BY created_at ASC
        """, (current_user_id,))

        users = cursor.fetchall()
        cursor.close()
        conn.close()

        # Convert to dictionary format with user_id as key
        users_dict = {}
        for user in users:
            users_dict[str(user['user_id'])] = {
                "user_id": user['user_id'],
                "full_name": user['full_name'],
                "address": user['address'],
                "email": user['email'],
                "phone_number": user['phone_number'],
                "user_type": user['user_type'],
                "status": user['status'],
                "gst_no": user['gst_no'],
                "company_name": user['company_name'],
                "pincode": user['pincode'],
                "distributor_code": user['distributor_code'],
                "dealer_code": user['dealer_code'],
                "created_at": user['created_at'].isoformat() if user['created_at'] else None
            }

        return jsonify(users_dict), 200

    except Exception as e:
        print("---- /admin/pending ERROR ----")
        traceback.print_exc()
        print("-----------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch pending requests",
            "error": str(e)
        }), 500

# ================================
# 🎯 NEW ENDPOINT FOR DISTRIBUTOR
# ================================
@app.route("/distributor/dealers", methods=["GET"])
def get_distributor_dealers():
    """
    Fetch all dealers under the current logged-in distributor
    
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
        
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # First, get the distributor's code
        cursor.execute("""
            SELECT distributor_code, full_name, company_name
            FROM user_signups
            WHERE user_id = %s AND user_type = 'distributor'
        """, (distributor_id,))
        
        distributor = cursor.fetchone()
        if not distributor:
            cursor.close()
            conn.close()
            return jsonify({
                "status": "error",
                "message": "Distributor not found"
            }), 404

        distributor_code = distributor['distributor_code']

        # Now fetch all dealers who have this distributor code
        cursor.execute("""
            SELECT 
                user_id,
                full_name,
                address,
                email,
                phone_number,
                gst_no,
                company_name,
                pincode,
                dealer_code,
                status,
                created_at
            FROM user_signups
            WHERE user_type = 'dealer' 
            AND distributor_code = %s
            ORDER BY created_at DESC
        """, (distributor_code,))

        dealers = cursor.fetchall()
        cursor.close()
        conn.close()

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
        print("---- /distributor/dealers ERROR ----")
        traceback.print_exc()
        print("-------------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch dealers",
            "error": str(e)
        }), 500


# ================================
# Admin Dashboard
# ================================
@app.route("/admin_dashboard", methods=["GET"])
def admin_dashboard():
    if 'user_type' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, full_name, address, email, status, user_type, gst_no, company_name, pincode, phone_number, distributor_code FROM user_signups WHERE (user_type = 'dealer' OR user_type = 'distributor') AND status = 'Pending'
    """)
    users = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("dashboard.html", users=users)

def send_email(to_email: str, subject: str, body_text: str) -> None:
    """
    Sends email using SMTP_CONFIG. Raises exception if fails.
    """
    to_email = (to_email or "").strip()
    if not to_email:
        raise ValueError("Recipient email is empty")

    host = SMTP_CONFIG["host"]
    port = SMTP_CONFIG["port"]
    username = SMTP_CONFIG["username"]
    password = SMTP_CONFIG["password"]
    from_email = SMTP_CONFIG["from_email"]
    use_tls = SMTP_CONFIG["use_tls"]
    debug = SMTP_CONFIG["debug"]

    if not host or not port:
        raise ValueError("SMTP_HOST/SMTP_PORT not configured")
    if not from_email:
        raise ValueError("SMTP_FROM_EMAIL (or SMTP_USERNAME) not configured")

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    # SSL Context
    if SMTP_CONFIG.get("skip_hostname_verify"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context()

    tls_server_name = (SMTP_CONFIG.get("tls_server_name") or host).strip()

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.set_debuglevel(1 if debug else 0)
        server.ehlo()

        if use_tls:
            # Use your custom STARTTLS wrapper to ensure SNI + hostname verification
            _smtp_starttls_with_server_name(server, context, tls_server_name)

        # login only if username provided
        if username:
            server.login(username, password)

        server.send_message(msg)


# ------------------------------------------------------------
# DB HELPERS
# ------------------------------------------------------------
def get_user_signup_details(user_id: int):
    """
    Fetch dealer/distributor email, name, type, and code from user_signups.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id,
                       COALESCE(full_name, '') as full_name,
                       COALESCE(email, '') as email,
                       COALESCE(user_type, '') as user_type,
                       COALESCE(dealer_code, '') as dealer_code,
                       COALESCE(distributor_code, '') as distributor_code
                FROM user_signups
                WHERE user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "full_name": row[1],
                "email": row[2],
                "user_type": row[3],
                "dealer_code": row[4],
                "distributor_code": row[5],
            }
    finally:
        conn.close()


def update_user_status(user_id: int, new_status: str) -> None:
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_signups
                SET status = %s
                WHERE user_id = %s
            """, (new_status, user_id))
        conn.commit()
    finally:
        conn.close()

@app.route("/approve/<int:user_id>", methods=["POST"])
def approve_dealer(user_id):
    # 1) Update status
    update_user_status(user_id, "Approved")

    # 2) Fetch email details
    info = get_user_signup_details(user_id)
    if not info:
        logger.warning("approve: user_id %s not found in user_signups", user_id)
        return redirect(url_for("admin_dashboard"))

    # 3) Send email
    try:
        name = info["full_name"].strip() or "User"
        utype = (info["user_type"].strip().lower() or "user")
        subject = "PGAK Account Approved"
        
        # Get the respective code based on user type
        if info["user_type"] == "dealer":
            code = info["dealer_code"]
        else:
            code = info["distributor_code"]
        
        body = (
            f"Hello {name},\n\n"
            f"Your {utype} account has been APPROVED.\n\n"
            f"Your {utype} code: {code}\n\n"
            f"You can now log in and use the portal with your credentials.\n\n"
            f"Please visit the portal and log in with the email you registered with: {info['email']}.\n\n"
            f"Regards,\n"
            f"PGAK Team"
        )
        send_email(info["email"], subject, body)
        logger.info("approve: email sent to %s (user_id=%s)", info["email"], user_id)
    except Exception as e:
        logger.exception("approve: email failed for user_id=%s, err=%s", user_id, e)

    return redirect(url_for("admin_dashboard"))


@app.route("/reject/<int:user_id>", methods=["POST"])
def reject_dealer(user_id):
    # 1) Update status
    update_user_status(user_id, "Rejected")

    # 2) Fetch email details
    info = get_user_signup_details(user_id)
    if not info:
        logger.warning("reject: user_id %s not found in user_signups", user_id)
        return redirect(url_for("admin_dashboard"))

    # 3) Send email
    try:
        name = info["full_name"].strip() or "User"
        utype = (info["user_type"].strip().lower() or "user")
        subject = "PGAK Account Rejected"
        
        # Get the respective code based on user type
        if info["user_type"] == "dealer":
            code = info["dealer_code"]
        else:
            code = info["distributor_code"]
        
        body = (
            f"Hello {name},\n\n"
            f"Your {utype} account has been REJECTED.\n\n"
            f"Your {utype} code: {code}\n\n"
            f"If you believe this decision is in error, please contact support at support@pgak.com.\n\n"
            f"Regards,\n"
            f"PGAK Team"
        )
        send_email(info["email"], subject, body)
        logger.info("reject: email sent to %s (user_id=%s)", info["email"], user_id)
    except Exception as e:
        logger.exception("reject: email failed for user_id=%s, err=%s", user_id, e)

    return redirect(url_for("admin_dashboard"))


@app.route("/dealer_dashboard", methods=["GET"])
def dealer_dashboard():
    if session.get("user_type") != "dealer":
        return redirect(url_for("login"))
    return render_template("dealer_dashboard.html", user_name=session.get("full_name"))


@app.route("/distributor_dashboard", methods=["GET"])
def distributor_dashboard():
    if session.get("user_type") != "distributor":
        return redirect(url_for("login"))
    return render_template("distributor_dashboard.html", user_name=session.get("full_name"))
# ================================
# Approve Dealer/Distributor
# ================================
# @app.route("/approve/<int:user_id>", methods=["POST"])
# def approve_dealer(user_id):
#     conn = psycopg2.connect(**DB_CONFIG)
#     cur = conn.cursor()

#     cur.execute("""
#         UPDATE user_signups
#         SET status = 'Approved'
#         WHERE user_id = %s
#     """, (user_id,))
#     conn.commit()

#     cur.close()
#     conn.close()

#     return redirect(url_for('admin_dashboard'))

# @app.route("/reject/<int:user_id>", methods=["POST"])
# def reject_dealer(user_id):
#     conn = psycopg2.connect(**DB_CONFIG)
#     cur = conn.cursor()

#     cur.execute("""
#         UPDATE user_signups
#         SET status = 'Rejected'
#         WHERE user_id = %s
#     """, (user_id,))
#     conn.commit()

#     cur.close()
#     conn.close()

#     return redirect(url_for('admin_dashboard'))

# @app.route("/dealer_dashboard", methods=["GET"])
# def dealer_dashboard():
#     if session.get("user_type") != "dealer":
#         return redirect(url_for("login"))
#     return render_template("dealer_dashboard.html", user_name=session.get("full_name"))

# @app.route("/distributor_dashboard", methods=["GET"])
# def distributor_dashboard():
#     if session.get("user_type") != "distributor":
#         return redirect(url_for("login"))
#     return render_template("distributor_dashboard.html", user_name=session.get("full_name"))

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)
    
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('sign_up.html')

    if request.method == "POST":
        contact = (request.form.get("contact") or "").strip()
        password = request.form.get("password") or ""

        if not contact or not password:
            return jsonify({"status": "error", "message": "Please enter email/phone and password"}), 400

        try:
            with psycopg2.connect(**DB_CONFIG) as conn:
                with conn.cursor() as cur:
                    if "@" in contact:
                        cur.execute("""
                            SELECT user_id, user_type, full_name, address, email, password, status, dealer_code
                            FROM user_signups
                            WHERE email = %s
                            LIMIT 1
                        """, (contact,))
                    else:
                        cur.execute("""
                            SELECT user_id, user_type, full_name, address, email, password, status, dealer_code
                            FROM user_signups
                            WHERE phone_number = %s
                            LIMIT 1
                        """, (contact,))

                    user = cur.fetchone()

                    if not user:
                        return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

                    user_id, user_type, full_name, address, email, db_hashed_password, status, dealer_code = user

                    try:
                        if not verify_password(password, db_hashed_password):
                            return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401
                    except ValueError:
                        return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

                    if (status or "").lower() == "pending":
                        return jsonify({"status": "error", "message": "Your account is pending admin approval"}), 403
                    elif (status or "").lower() == "rejected":
                        return jsonify({"status": "error", "message": "Your account has been rejected"}), 403
                    elif (status or "").lower() != "approved":
                        return jsonify({"status": "error", "message": "Your account is not approved yet"}), 403

                    # ✅ Store basic session data
                    session["user_type"] = user_type
                    session["user_id"] = user_id
                    session["full_name"] = full_name
                    session["email"] = email
                    session["dealer_code"] = dealer_code
                    
                    # ✅ Session permanent rakho (24 hrs globally set hai upar)
                    session.permanent = True
                    
                    # ✅ NEW: For dealers, store credentials for re-authentication
                    external_token_success = False
                    if user_type.lower() == "dealer" and dealer_code:
                        # ✅ Store credentials in session for future token fetching
                        session["dealer_email"] = email
                        session["dealer_password"] = password  # Store original password
                        session["dealer_code"] = dealer_code
                        
                        try:
                            print(f"🔐 Getting initial access token for dealer: {email}")
                            
                            external_login_url = "https://api.pgak.co.in/auth/login"
                            external_login_payload = {
                                "email": email,
                                "password": password,
                                "dealer_code": dealer_code
                            }
                            
                            ext_response = requests.post(
                                external_login_url,
                                json=external_login_payload,
                                timeout=15,
                                verify=True
                            )
                            
                            print(f"📡 External API Response Status: {ext_response.status_code}")
                            print(f"📡 External API Response Body: {ext_response.text[:500]}")
                            
                            if ext_response.status_code == 200:
                                ext_data = ext_response.json()
                                access_token = ext_data.get("access_token")
                                
                                if access_token:
                                    # ✅ Store initial token in session
                                    session["external_access_token"] = access_token
                                    external_token_success = True
                                    
                                    print(f"✅ External API authentication successful for dealer {email}")
                                    print(f"✅ Initial token stored: {access_token[:50]}...")
                                else:
                                    print(f"⚠️ No access_token in response: {ext_data}")
                            else:
                                print(f"❌ External API login failed: {ext_response.status_code}")
                                print(f"❌ Response: {ext_response.text}")
                                
                        except requests.exceptions.Timeout:
                            print(f"⏱️ External API timeout for dealer {email}")
                        except requests.exceptions.ConnectionError as e:
                            print(f"🔌 External API connection error: {e}")
                        except Exception as e:
                            print(f"❌ External API error: {e}")
                            traceback.print_exc()
                    
                    # ✅ Mark session as modified
                    session.modified = True
                    
                    # ✅ Update login status in DB
                    cur.execute("""
                        UPDATE user_signups
                        SET is_login = TRUE
                        WHERE user_id = %s
                    """, (session["user_id"],))
                    conn.commit()

                    # ✅ Determine redirect URL
                    redirect_url = url_for("admin_dashboard")
                    if user_type.lower() == "dealer":
                        redirect_url = url_for("dealer_dashboard")
                    elif user_type.lower() == "distributor":
                        redirect_url = url_for("distributor_dashboard")

                    # ✅ Return success response
                    response_data = {
                        "status": "success",
                        "redirect": redirect_url,
                        "user_type": user_type
                    }
                    
                    # ✅ Add warning if external token failed for dealer
                    if user_type.lower() == "dealer" and not external_token_success:
                        response_data["warning"] = "External API authentication failed. Customer data may not be available."
                        print("⚠️ Warning: Dealer logged in but external API authentication failed")
                    
                    print(f"✅ Login successful for {user_type}: {email}")
                    return jsonify(response_data), 200

        except Exception as e:
            print("---- /login ERROR ----")
            traceback.print_exc()
            print("----------------------")
            return jsonify({"status": "error", "message": "Login failed. Please try again later"}), 500
# ================================
# Login Route
# ================================
# @app.route('/login', methods=['GET', 'POST'])
# def login():
#     if request.method == 'GET':
#         return render_template('sign_up.html')

#     if request.method == "POST":
#         contact = (request.form.get("contact") or "").strip()
#         password = request.form.get("password") or ""

#         if not contact or not password:
#             return jsonify({"status": "error", "message": "Please enter email/phone and password"}), 400

#         try:
#             with psycopg2.connect(**DB_CONFIG) as conn:
#                 with conn.cursor() as cur:
#                     # ✅ CHANGE: also SELECT email so we can store it in session
#                     if "@" in contact:
#                         cur.execute("""
#                             SELECT user_id, user_type, full_name, address, email, password, status
#                             FROM user_signups
#                             WHERE email = %s
#                             LIMIT 1
#                         """, (contact,))
#                     else:
#                         cur.execute("""
#                             SELECT user_id, user_type, full_name, address, email, password, status
#                             FROM user_signups
#                             WHERE phone_number = %s
#                             LIMIT 1
#                         """, (contact,))

#                     user = cur.fetchone()

#                     if not user:
#                         return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

#                     user_id, user_type, full_name, address, email, db_hashed_password, status = user

#                     try:
#                         if not verify_password(password, db_hashed_password):
#                             return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401
#                     except ValueError:
#                         # Handle malformed hash as invalid credentials
#                         return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

#                     if (status or "").lower() == "pending":
#                         return jsonify({"status": "error", "message": "Your account is pending admin approval"}), 403
#                     elif (status or "").lower() == "rejected":
#                         return jsonify({"status": "error", "message": "Your account has been rejected"}), 403
#                     elif (status or "").lower() != "approved":
#                         return jsonify({"status": "error", "message": "Your account is not approved yet"}), 403

#                     # ✅ session saved fields used by /api/current-user
#                     session["user_type"] = user_type
#                     session["user_id"] = user_id
#                     session["full_name"] = full_name
#                     session["email"] = email  # ✅ NEW

#                     cur.execute("""
#                         UPDATE user_signups
#                         SET is_login = TRUE
#                         WHERE user_id = %s
#                     """, (session["user_id"],))
#                     conn.commit()

#                     redirect_url = url_for("admin_dashboard")
#                     if user_type.lower() == "dealer":
#                         redirect_url = url_for("dealer_dashboard")
#                     elif user_type.lower() == "distributor":
#                         redirect_url = url_for("distributor_dashboard")

#                     return jsonify({
#                         "status": "success",
#                         "redirect": redirect_url
#                     }), 200

#         except Exception as e:
#             print("---- /login ERROR ----")
#             traceback.print_exc()
#             print("----------------------")
#             return jsonify({"status": "error", "message": "Login failed. Please try again later"}), 500


@app.route("/api/current-user", methods=["GET"])
def api_current_user():
    """
    Returns the currently logged-in user's profile for frontend sidebar.
    Uses Flask session cookie to identify user.
    
    Response:
      200 -> { user_id, full_name, address, email, user_type }
      401 -> not logged in
    """
    try:
        user_id = session.get("user_id")
        user_type = session.get("user_type")

        if not user_id or not user_type:
            return jsonify({
                "status": "error",
                "message": "Not logged in"
            }), 401

        # Fast path (if email was stored in session)
        sess_full_name = session.get("full_name")
        sess_email = session.get("email")

        # If session already has all needed fields, return immediately
        if sess_full_name and sess_email and user_type:
            return jsonify({
                "user_id": user_id,
                "full_name": sess_full_name,
                "email": sess_email,
                "user_type": user_type
            }), 200

        # Otherwise fetch from DB (reliable source)
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT user_id, full_name, email, user_type, status
            FROM user_signups
            WHERE user_id = %s
            LIMIT 1
        """, (user_id,))

        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user:
            # Session exists but user not found (deleted user or bad session)
            session.clear()
            return jsonify({
                "status": "error",
                "message": "Session invalid"
            }), 401

        # Optional: block non-approved users
        status = (user.get("status") or "").lower()
        if status and status != "approved":
            return jsonify({
                "status": "error",
                "message": "Account not approved"
            }), 403

        # Store into session for next time (reduces DB calls)
        session["full_name"] = user.get("full_name")
        session["email"] = user.get("email")
        session["user_type"] = user.get("user_type")
        session["user_id"] = user.get("user_id")

        return jsonify({
            "user_id": user.get("user_id"),
            "full_name": user.get("full_name"),
            "email": user.get("email"),
            "user_type": user.get("user_type")
        }), 200

    except Exception as e:
        print("---- /api/current-user ERROR ----")
        traceback.print_exc()
        print("---------------------------------")
        return jsonify({
            "status": "error",
            "message": "Server error",
            "error": str(e)
        }), 500

# @app.route("/logout", methods=["GET"])
# def logout():
#     session.clear()
#     return redirect(url_for('login'))
@app.route("/logout", methods=["GET"])
def logout():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.close()
    session.clear()
    return redirect(url_for('login'))

# ================================
# DEVICE MONITORING SYSTEM
# ================================
# ==============================
# DB HELPERS
# ==============================
# def write_status_to_db(info: dict, status: str):
#     hostname = info.get("Hostname")

#     conn = psycopg2.connect(**DB_CONFIG)
#     cur = conn.cursor()

#     cur.execute(
#         "SELECT 1 FROM system_information WHERE hostname=%s",
#         (hostname,)
#     )
#     exists = cur.fetchone()

#     if not exists:
#         cur.execute("""
#             INSERT INTO system_information (
#                 hostname, os, os_version, kernel_version,
#                 make, model, serial_number, processor,
#                 machine_type, mac_addresses, ip_address,
#                 status, created_at
#             )
#             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
#         """, (
#             hostname,
#             info.get("OS"),
#             info.get("OS Version"),
#             info.get("Kernel Version"),
#             info.get("Make"),
#             info.get("Model"),
#             info.get("Serial Number"),
#             info.get("Processor"),
#             info.get("Machine (OS Type)"),
#             Json(info.get("MAC Addresses")),
#             Json(info.get("IP Address")),
#             status
#         ))
#     else:
#         cur.execute("""
#             UPDATE system_information
#             SET status=%s
#             WHERE hostname=%s
#         """, (status, hostname))

#     conn.commit()
#     cur.close()
#     conn.close()


# def update_device_status(info: dict, sys_status: str):
#     new_status = "ONLINE" if sys_status == "ACTIVE" else "OFFLINE"

#     ip_map = info.get("IP Address") or {}
#     ip_address = next(iter(ip_map.values()), None)
#     hostname = info.get("Hostname")

#     if not ip_address:
#         return

#     conn = psycopg2.connect(**DB_CONFIG)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT status, online_at, offline_at
#         FROM device_status
#         WHERE ip_address=%s
#         ORDER BY last_change_at DESC
#         LIMIT 1
#     """, (ip_address,))
#     row = cur.fetchone()

#     now = datetime.now()

#     if row is None:
#         if new_status == "ONLINE":
#             cur.execute("""
#                 INSERT INTO device_status
#                 (ip_address, hostname, status, online_at, last_change_at)
#                 VALUES (%s,%s,'ONLINE',%s,NOW())
#             """, (ip_address, hostname, now))
#         else:
#             cur.execute("""
#                 INSERT INTO device_status
#                 (ip_address, hostname, status, offline_at, last_change_at)
#                 VALUES (%s,%s,'OFFLINE',%s,NOW())
#             """, (ip_address, hostname, now))
#     else:
#         last_status, online_at, offline_at = row

#         if last_status != new_status:
#             if new_status == "ONLINE":
#                 offline_seconds = int((now - offline_at).total_seconds()) if offline_at else 0
#                 cur.execute("""
#                     INSERT INTO device_status
#                     (ip_address, hostname, status, online_at,
#                      offline_duration_seconds, last_change_at)
#                     VALUES (%s,%s,'ONLINE',%s,%s,NOW())
#                 """, (ip_address, hostname, now, offline_seconds))
#             else:
#                 cur.execute("""
#                     INSERT INTO device_status
#                     (ip_address, hostname, status, offline_at, last_change_at)
#                     VALUES (%s,%s,'OFFLINE',%s,NOW())
#                 """, (ip_address, hostname, now))

#     conn.commit()
#     cur.close()
#     conn.close()

# # ==============================
# # API ROUTES
# # ==============================
# @app.route("/system_info", methods=["POST"])
# def system_info():
#     data = request.get_json(force=True) or {}
#     hostname = data.get("Hostname", "UNKNOWN")

#     DEVICES[hostname] = {
#         "status": "ACTIVE",
#         "last_seen_ts": time.time(),
#         "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#         "info": data
#     }

#     write_status_to_db(data, "ACTIVE")
#     update_device_status(data, "ACTIVE")

#     return jsonify({"ok": True})
def scan_device(ip_address):
    """
    Scan a device using its IP address.
    Returns device info with the IP.
    """
    device_info = {
        "ip_address": ip_address,
        "status": "online"
    }
    print(device_info,"--------------------9999999999999999999999999")
    return device_info


def is_onvif_device(ip_address):
    """
    Check if the device supports ONVIF (this can be based on certain characteristics or ports).
    You can enhance this with more sophisticated checks such as port availability or sending a discovery request.

    Args:
        ip_address (str): The IP address of the device.

    Returns:
        bool: True if ONVIF protocol is supported by the device.
    """
    # Example check (replace with actual ONVIF detection logic, such as checking port 8080 or a probe request)
    return ip_address.startswith("192.168.")  # Dummy condition: modify based on actual use case

def scan_onvif_device(ip_address):
    """
    Scans a device using the ONVIF protocol.

    Args:
        ip_address (str): The IP address of the device.

    Returns:
        dict: ONVIF device details (replace with actual scanning logic).
    """
    # Example ONVIF scanning logic (replace with actual implementation)
    return {
        "device_id": f"ONVIF-{ip_address}",
        "make": "ONVIFDevice",
        "model": "ONVIFModel",
        "ip_address": ip_address,
        "protocol": "ONVIF",
        "status": "online"
    }

def is_onvif_device(ip_address):
    """
    Check if the device supports ONVIF (this can be based on certain characteristics or ports).
    For now, it's a dummy check that you can expand.

    Args:
        ip_address (str): The IP address of the device.

    Returns:
        bool: True if ONVIF protocol is supported by the device.
    """
    if isinstance(ip_address, str):  # Ensure that ip_address is a string
        # Example check (you can use more sophisticated checks like port availability)
        return ip_address.startswith("192.168.")  # Dummy condition: modify based on actual use case
    return False

def is_onvif_device(ip_address):
    """
    Check if the device supports ONVIF (this can be based on certain characteristics or ports).
    For now, it's a dummy check that you can expand.

    Args:
        ip_address (str): The IP address of the device.

    Returns:
        bool: True if ONVIF protocol is supported by the device.
    """
    if isinstance(ip_address, str):  # Ensure that ip_address is a string
        # Example check (you can use more sophisticated checks like port availability)
        return ip_address.startswith("192.168.")  # Dummy condition: modify based on actual use case
    return False

def is_http_device(ip_address):
    """
    Check if the device supports HTTP-based scanning (e.g., REST API).
    
    Args:
        ip_address (str): The IP address of the device.

    Returns:
        bool: True if HTTP protocol is supported by the device.
    """
    if isinstance(ip_address, str):  # Ensure that ip_address is a string
        # Example check (you can use more sophisticated checks like port availability)
        return ip_address.startswith("192.168.")  # Dummy condition: modify based on actual use case
    return False



def scan_http_device(ip_address):
    """
    Scans a device using HTTP-based methods (e.g., REST API).

    Args:
        ip_address (str): The IP address of the device.

    Returns:
        dict: HTTP device details (replace with actual scanning logic).
    """
    # Example HTTP scanning logic (replace with actual implementation)
    return {
        "device_id": f"HTTP-{ip_address}",
        "make": "HTTPDevice",
        "model": "HTTPModel",
        "ip_address": ip_address,
        "protocol": "HTTP",
        "status": "online"
    }

@app.route('/api/onvif/scan-with-ip', methods=['POST'])
def scan_device_with_ip():
    """
    Endpoint to scan a device using its IP address ONLY.
    Expects a JSON body with:
    {
        "ip_address": {"eth0": "192.168.2.10", "lo": "127.0.0.1"}
    }
    """
    try:
        data = request.get_json()
        ip_address = data.get('ip_address')

        # Ensure ip_address is a dictionary
        if not ip_address or not isinstance(ip_address, dict):
            print(f"Invalid IP address format: {ip_address}")
            return jsonify({"success": False, "message": "IP address must be a valid JSON object"}), 400
        
        # Extract the first valid IP address from the dictionary
        ip_address_value = None
        for key, value in ip_address.items():
            if isinstance(value, str) and value.count('.') == 3:
                ip_address_value = value
                break
        
        if not ip_address_value:
            print(f"No valid IP address found in the JSON: {ip_address}")
            return jsonify({"success": False, "message": "No valid IP address found"}), 400
        
        # ✅ FIX: SIRF YEH IP PE SCAN KARO, AUR KUCH NAHI
        print(f"🔍 Scanning ONLY this IP: {ip_address_value}")
        device_info = scan_device(ip_address_value)  # SIRF EK HI CALL

        if device_info and device_info.get("status") == "online":
            print(f"✅ Device scan successful for IP: {ip_address_value}")
            return jsonify({
                "success": True,
                "message": "Device scan successful",
                "device_info": device_info
            }), 200
        else:
            print(f"❌ Device offline or not found: {ip_address_value}")
            return jsonify({
                "success": False,
                "message": "Device scan failed or device is offline"
            }), 500

    except Exception as e:
        print(f"❌ Error scanning device: {e}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

def ensure_asset_exists(cur, serial: str, qr_status: str = "PENDING"):
    """
    Ensures a row exists in public.assets for given serial.

    Assumed assets schema (your requirement):
      - assets(serial, qr_status, created_at)
      - id auto generated
    NOTE:
      - We DO NOT use ON CONFLICT unless serial is UNIQUE in DB.
      - We protect the outer transaction using SAVEPOINT so any failure
        does NOT abort the whole /system_info transaction.
    """
    serial = (serial or "").strip()
    qr_status = (qr_status or "PENDING").strip().upper()

    if not serial:
        return

    # Protect outer transaction (so even if insert fails, we rollback only this part)
    cur.execute("SAVEPOINT sp_asset;")

    try:
        # 1) Try insert assuming serial UNIQUE (fast path)
        try:
            cur.execute(
                """
                INSERT INTO public.assets (serial, qr_status, created_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (serial) DO NOTHING;
                """,
                (serial, qr_status),
            )
            # success
            cur.execute("RELEASE SAVEPOINT sp_asset;")
            return
        except Exception:
            # ON CONFLICT may fail if serial not UNIQUE or column mismatch
            cur.execute("ROLLBACK TO SAVEPOINT sp_asset;")

        # 2) Fallback: SELECT guard (works even if serial is not UNIQUE)
        cur.execute("SELECT 1 FROM public.assets WHERE serial=%s LIMIT 1;", (serial,))
        if cur.fetchone() is None:
            cur.execute(
                """
                INSERT INTO public.assets (serial, qr_status, created_at)
                VALUES (%s, %s, NOW());
                """,
                (serial, qr_status),
            )

        cur.execute("RELEASE SAVEPOINT sp_asset;")
        return

    except Exception:
        # If anything fails here, rollback only this savepoint to keep transaction alive
        try:
            cur.execute("ROLLBACK TO SAVEPOINT sp_asset;")
            cur.execute("RELEASE SAVEPOINT sp_asset;")
        except Exception:
            pass
        # Don't crash system_info for asset insert failure
        return

def write_status_to_db(info: dict, status: str):
    hostname = info.get("Hostname")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute(
        "SELECT 1 FROM system_information WHERE hostname=%s",
        (hostname,)
    )
    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO system_information (
                hostname, os, os_version, kernel_version,
                make, model, serial_number, processor,
                machine_type, mac_addresses, ip_address,
                status, created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        """, (
            hostname,
            info.get("OS"),
            info.get("OS Version"),
            info.get("Kernel Version"),
            info.get("Make"),
            info.get("Model"),
            info.get("Serial Number"),
            info.get("Processor"),
            (info.get("Machine (OS Type)") or info.get("Machine") or info.get("Machine Type")),
            Json(info.get("MAC Addresses")),
            Json(info.get("IP Address")),
            status
        ))
        
    else:

        machine_val = (info.get("Machine (OS Type)") or info.get("Machine") or info.get("Machine Type"))
        cur.execute("""
            UPDATE system_information
            SET
                status=%s,
                os = COALESCE(NULLIF(os,''), %s),
                os_version = COALESCE(NULLIF(os_version,''), %s),
                kernel_version = COALESCE(NULLIF(kernel_version,''), %s),
                make = COALESCE(NULLIF(make,''), %s),
                model = COALESCE(NULLIF(model,''), %s),
                serial_number = COALESCE(NULLIF(serial_number,''), %s),
                processor = COALESCE(NULLIF(processor,''), %s),
                machine_type = COALESCE(NULLIF(machine_type,''), %s),
                mac_addresses = COALESCE(mac_addresses, %s),
                ip_address = %s
            WHERE hostname=%s
        """, (
            status,
            info.get("OS"),
            info.get("OS Version"),
            info.get("Kernel Version"),
            info.get("Make"),
            info.get("Model"),
            info.get("Serial Number"),
            info.get("Processor"),
            machine_val,
            Json(info.get("MAC Addresses")),
            Json(info.get("IP Address")),
            hostname
        ))
    # print(info.get("IP Address"),"0000000000000000000000000000000000000000000000")
    conn.commit()
    cur.close()
    conn.close()


# def update_device_status(info: dict, sys_status: str):
#     new_status = "ONLINE" if sys_status == "ACTIVE" else "OFFLINE"

#     ip_map = info.get("IP Address") or {}
#     ip_address = _extract_first_ip(ip_map)
#     hostname = info.get("Hostname")

#     if not ip_address:
#         return

#     conn = psycopg2.connect(**DB_CONFIG)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT status, online_at, offline_at
#         FROM device_status
#         WHERE ip_address=%s
#         ORDER BY last_change_at DESC
#         LIMIT 1
#     """, (ip_address,))
#     row = cur.fetchone()

#     now = datetime.now()

#     if row is None:
#         if new_status == "ONLINE":
#             cur.execute("""
#                 INSERT INTO device_status
#                 (ip_address, hostname, status, online_at, last_change_at)
#                 VALUES (%s,%s,'ONLINE',%s,NOW())
#             """, (ip_address, hostname, now))
#         else:
#             cur.execute("""
#                 INSERT INTO device_status
#                 (ip_address, hostname, status, offline_at, last_change_at)
#                 VALUES (%s,%s,'OFFLINE',%s,NOW())
#             """, (ip_address, hostname, now))
#     else:
#         last_status, online_at, offline_at = row

#         if last_status != new_status:
#             if new_status == "ONLINE":
#                 offline_seconds = int((now - offline_at).total_seconds()) if offline_at else 0
#                 cur.execute("""
#                     INSERT INTO device_status
#                     (ip_address, hostname, status, online_at,
#                      offline_duration_seconds, last_change_at)
#                     VALUES (%s,%s,'ONLINE',%s,%s,NOW())
#                 """, (ip_address, hostname, now, offline_seconds))
#             else:
#                 cur.execute("""
#                     INSERT INTO device_status
#                     (ip_address, hostname, status, offline_at, last_change_at)
#                     VALUES (%s,%s,'OFFLINE',%s,NOW())
#                 """, (ip_address, hostname, now))

#     conn.commit()
#     cur.close()
#     conn.close()
def update_device_status(info: dict, sys_status: str):
    new_status = "ONLINE" if sys_status == "ACTIVE" else "OFFLINE"

    ip_map = info.get("IP Address") or {}
    ip_address = _extract_first_ip(ip_map)
    hostname = info.get("Hostname")

    if not ip_address:
        return

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Lookup by HOSTNAME instead of IP
    cur.execute("""
        SELECT status, online_at, offline_at
        FROM device_status
        WHERE hostname=%s
        ORDER BY last_change_at DESC
        LIMIT 1
    """, (hostname,))
    row = cur.fetchone()

    now = datetime.now()

    if row is None:
        # First time this device is seen — INSERT
        if new_status == "ONLINE":
            cur.execute("""
                INSERT INTO device_status
                (ip_address, hostname, status, online_at, last_change_at)
                VALUES (%s,%s,'ONLINE',%s,NOW())
            """, (ip_address, hostname, now))
        else:
            cur.execute("""
                INSERT INTO device_status
                (ip_address, hostname, status, offline_at, last_change_at)
                VALUES (%s,%s,'OFFLINE',%s,NOW())
            """, (ip_address, hostname, now))
    else:
        last_status, online_at, offline_at = row

        if last_status != new_status:
            if new_status == "ONLINE":
                offline_seconds = int((now - offline_at).total_seconds()) if offline_at else 0
                cur.execute("""
                    UPDATE device_status
                    SET ip_address=%s, status='ONLINE', online_at=%s,
                        offline_duration_seconds=%s, last_change_at=NOW()
                    WHERE hostname=%s
                """, (ip_address, now, offline_seconds, hostname))
            else:
                cur.execute("""
                    UPDATE device_status
                    SET ip_address=%s, status='OFFLINE', offline_at=%s, last_change_at=NOW()
                    WHERE hostname=%s
                """, (ip_address, now, hostname))
        else:
            # Same status but IP may have changed — update IP
            cur.execute("""
                UPDATE device_status
                SET ip_address=%s, last_change_at=NOW()
                WHERE hostname=%s
            """, (ip_address, hostname))

    conn.commit()
    cur.close()
    conn.close()
# ==============================
# API ROUTES
# ==============================
@app.route("/system_info", methods=["POST"])
def system_info():
    data = request.get_json(force=True) or {}
    hostname = data.get("Hostname", "UNKNOWN")

    DEVICES[hostname] = {
        "status": "ACTIVE",
        "last_seen_ts": time.time(),
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "info": data
    }

    write_status_to_db(data, "ACTIVE")
    update_device_status(data, "ACTIVE")

    return jsonify({"ok": True})

# @app.route("/devices", methods=["GET"])
# def devices():
#     """
#     Get devices with optional filtering
#     ?view=online  -> Returns only ACTIVE devices
#     ?view=all     -> Returns ACTIVE + OFFLINE devices
#     Default: online (only ACTIVE devices)
#     """
#     try:
#         filter_type = (request.args.get("filter", "online") or "online").strip().lower()
#         if filter_type not in ("all", "online"):
#             filter_type = "online"
#             # Return ALL devices (both ACTIVE and OFFLINE)
#             response_devices = DEVICES
#         else:  # default to 'online'
#             # Return only ACTIVE devices
#             response_devices = {
#                 hostname: data 
#                 for hostname, data in DEVICES.items() 
#                 if data.get('status') == 'ACTIVE'
#             }
        
#         return jsonify({
#             "view": filter_type,
#             "devices": response_devices,
#             "total_count": len(DEVICES),
#             "online_count": len([d for d in DEVICES.values() if d.get('status') == 'ACTIVE']),
#             "offline_count": len([d for d in DEVICES.values() if d.get('status') != 'ACTIVE'])
#         })
#     except Exception as e:
#         print(f"Error in /devices endpoint: {str(e)}")
#         traceback.print_exc()
#         return jsonify({"error": str(e), "devices": {}}), 500

#1223345aditi



# @app.route("/devices_status", methods=["GET"])
# def device():
#     """
#     Get devices list with optional filtering based on system_information table
    
#     Query Parameters:
#     - view: 'online' or 'all' (default: 'online')
    
#     Returns:
#     - If view='online': Only ONLINE devices (status='ONLINE' in system_information table)
#     - If view='all': All devices (ONLINE + OFFLINE from system_information table)
#     """
#     try:
#         # Get the 'view' parameter from query string (default: 'online')
#         view = (request.args.get("view", "online") or "online").strip().lower()
#         if view not in ("all", "online"):
#             view = "online"
        
#         # Query system_information table to get device status
#         conn = None
#         cursor = None
        
#         try:
#             # Connect to database
#             conn = psycopg2.connect(**DB_CONFIG)
#             cursor = conn.cursor()
            
#             # Execute query - CORRECT SYNTAX
#             query = """
#                 SELECT device_id, hostname, status, last_seen_at 
#                 FROM system_information
#             """
#             cursor.execute(query)
#             system_info_records = cursor.fetchall()
            
#             # Close cursor and connection
#             cursor.close()
#             conn.close()
            
#             # Create a mapping of device_id -> device info
#             devices_from_db = {}
#             for row in system_info_records:
#                 device_id = row[0]  # device_id
#                 hostname = row[1]   # hostname
#                 status = row[2]     # status (ONLINE or OFFLINE)
#                 last_seen_at = row[3]  # last_seen_at
                
#                 devices_from_db[device_id] = {
#                     'device_id': device_id,
#                     'hostname': hostname,
#                     'status': status,  # This comes directly from database
#                     'last_seen_at': str(last_seen_at) if last_seen_at else None
#                 }
        
#         except psycopg2.Error as db_error:
#             print(f"Database error: {str(db_error)}")
#             if cursor:
#                 cursor.close()
#             if conn:
#                 conn.close()
            
#             return jsonify({
#                 "status": "error",
#                 "message": f"Failed to query system_information table: {str(db_error)}",
#                 "devices": {}
#             }), 500
        
#         # Filter based on view parameter
#         if view == 'online':
#             # Show ONLY devices with status = 'ONLINE'
#             filtered_devices = {}
#             for device_id, device_info in devices_from_db.items():
#                 if device_info.get('status') == 'ONLINE':
#                     filtered_devices[device_id] = device_info
            
#             return jsonify({
#                 "status": "success",
#                 "view": "online",
#                 "count": len(filtered_devices),
#                 "message": f"Showing {len(filtered_devices)} online devices",
#                 "devices": filtered_devices
#             })
        
#         # If view is 'all', return ALL devices (ONLINE + OFFLINE)
#         elif view == 'all':
#             online_count = sum(1 for d in devices_from_db.values() if d.get('status') == 'ONLINE')
#             offline_count = len(devices_from_db) - online_count
            
#             return jsonify({
#                 "status": "success",
#                 "view": "all",
#                 "total_devices": len(devices_from_db),
#                 "online_count": online_count,
#                 "offline_count": offline_count,
#                 "message": f"Total: {len(devices_from_db)} devices ({online_count} ONLINE, {offline_count} OFFLINE)",
#                 "devices": devices_from_db
#             })
        
#         else:
#             return jsonify({
#                 "status": "error",
#                 "message": f"Invalid view parameter: {view}. Use 'online' or 'all'",
#                 "devices": {}
#             }), 400
    
#     except Exception as e:
#         print(f"Error in /devices_status endpoint: {str(e)}")
#         return jsonify({
#             "status": "error",
#             "message": str(e),
#             "devices": {}
#         }), 500
@app.get("/devices_status")
def api_device_status():
    """Return latest device status per device from `device_status`.

    Query param:
      filter = online | all
    Default = online

    This endpoint powers the Devices table + "Show Details".

    What it returns:
    - One latest row per ip_address from device_status
    - `info` object joined from system_information (so UI doesn't show N/A)
    - JSON-safe datetime strings
    """

    filter_type = (request.args.get("filter", "online") or "online").strip().lower()
    if filter_type not in ("all", "online"):
        filter_type = "online"

    def _dt_str(v):
        try:
            from datetime import datetime
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        return v

    # NOTE:
    # - latest: pick most recent status per ip_address
    # - join system_information by hostname to provide full device details
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (ds.ip_address)
                ds.id,
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
            l.id,
            l.ip_address,
            l.hostname,
            l.status,
            CASE
                WHEN l.status = 'ONLINE' THEN l.online_at
                WHEN l.status = 'OFFLINE' THEN l.offline_at
                ELSE l.last_change_at
            END AS last_seen,
            l.last_change_at,

            si.os,
            si.os_version,
            si.kernel_version,
            si.make,
            si.model,
            si.serial_number,
            si.processor,
            si.machine_type,
            si.mac_addresses,
            si.ip_address AS system_ip_address

        FROM latest l
        LEFT JOIN system_information si
            ON si.hostname = l.hostname

        WHERE (%s = 'all' OR l.status = 'ONLINE')

        ORDER BY l.hostname ASC NULLS LAST, l.ip_address ASC NULLS LAST
    """

    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (filter_type,))
                rows = cur.fetchall() or []

        out = []
        for r in rows:
            # Build info object in the exact keys UI expects
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

            # IP addresses: prefer system_information JSON, but always set Primary
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
                'id': r.get('id'),
                'ip_address': r.get('ip_address'),
                'hostname': r.get('hostname'),
                'status': r.get('status'),
                'last_seen': _dt_str(r.get('last_seen')),
                'last_change_at': _dt_str(r.get('last_change_at')),
                'info': info
            }

            # JSON-safe conversion for any leftover datetime values
            for k, v in list(device.items()):
                device[k] = _dt_str(v)

            out.append(device)

        return jsonify({
            'status': 'success',
            'filter': filter_type,
            'count': len(out),
            'devices': out
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    
                 
@app.route("/devices", methods=["GET"])
def devices():
    return jsonify(DEVICES)
# ==============================
# Device Discovery Endpoint
# ==============================
# @app.route("/device_discovery", methods=["POST"])
# def discover_device():
#     data = request.get_json(force=True) or {}
#     device_ip = data.get("IP", "UNKNOWN")
    
#     # Store the device's discovery info in DISCOVERED dictionary
#     DISCOVERED[device_ip] = {
#         "status": "ACTIVE",
#         "last_seen_ts": time.time(),
#         "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#         "info": data
#     }

#     # Optionally, save this information to the database (mock functions)
#     write_status_to_db(data, "ACTIVE")
#     update_device_status(data, "ACTIVE")

#     # Respond with success
#     return jsonify({"ok": True, "message": "Device discovery info saved", "device_ip": device_ip})


# @app.route("/devices", methods=["GET"])
# def devices():
#     # Return all system info and discovered devices
#     return jsonify(DEVICES)

SCAN_ALL_AGENTS = os.getenv("SCAN_ALL_AGENTS", "true").strip().lower() in ("1", "true", "yes")
AGENT_VERIFY_SSL = os.getenv("AGENT_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")
AGENT_CA_BUNDLE = os.getenv("AGENT_CA_BUNDLE", "").strip()


def normalize_agent_url(agent_scheme: str, agent_ip: str, agent_port: int, path: str) -> str:
    base = f"{agent_scheme}://{agent_ip}:{agent_port}/"
    return urljoin(base, path.lstrip("/"))

def agent_verify_value():
    if AGENT_VERIFY_SSL:
        return AGENT_CA_BUNDLE if AGENT_CA_BUNDLE else True
    return False  # self-signed allowed

AGENT_VERIFY = agent_verify_value()

# ================================
# IN-MEMORY STORE
# ================================
AGENTS_LOCK = threading.Lock()
AGENTS = {}

"""
AGENTS[key] = {
  "agent_ip": "...",
  "agent_scheme": "https",
  "agent_port": 5001,
  "last_seen_ts": ...,
  "last_seen": "...",
  "status": "ACTIVE",
  "info": {...payload from client...}
}
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_remote_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

from urllib.parse import urljoin

def normalize_agent_url(agent_scheme: str, agent_ip: str, agent_port: int, path: str) -> str:
    base = f"{agent_scheme}://{agent_ip}:{agent_port}/"
    return urljoin(base, path.lstrip("/"))








# ================================
# HEALTH
# ================================


# DEFAULT_AGENT_SCHEME = os.getenv("DEFAULT_AGENT_SCHEME", "https").strip().lower()
# DEFAULT_AGENT_PORT = int(os.getenv("DEFAULT_AGENT_PORT", "5001"))

# ================================
# RECEIVE HEARTBEAT FROM CLIENT
# ================================
@app.route("/device_discovery", methods=["POST"])
def device_discovery():
    """
    ✅ FIXED: Direct JOIN query for latest updated device
    Device discovery will run ONLY on the last updated device from device_master
    """
    global AGENTS, AGENTS_LOCK
    payload = request.get_json(silent=True)
    print(payload,")))))))))))))))))))))))))))))))))))))))))))))))))))))))")

    if payload is None:
        raw = request.get_data(as_text=True)
        return jsonify({
            "ok": False,
            "message": "Invalid/missing JSON. Send Content-Type: application/json",
            "content_type": request.content_type,
            "raw_preview": (raw[:500] if raw else "")
        }), 400

    # Extract IP from payload
    agent_ip = payload.get("IP") or payload.get("ip") or get_remote_ip()
    agent_scheme = (payload.get("agent_scheme") or DEFAULT_AGENT_SCHEME).strip().lower()
    agent_port = int(payload.get("agent_port") or DEFAULT_AGENT_PORT)

    # ✅ CRITICAL FIX: Single JOIN query to get latest updated device with IP
    TARGET_IP = None
    TARGET_SERIAL = None
    UPDATED_AT = None
    
    try:
        with psycopg2.connect(_build_dsn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # ✅ NEW APPROACH: One query with JOIN
                # Directly fetches the device with latest updated_at AND its IP
                cur.execute(
                    """
                    SELECT 
                        dm.serial_number,
                        dm.updated_at,
                        si.ip_address,
                        si.created_at as ip_created_at
                    FROM public.device_master dm
                    INNER JOIN public.system_information si 
                        ON dm.serial_number = si.serial_number
                    WHERE dm.serial_number IS NOT NULL
                        AND si.ip_address IS NOT NULL
                    ORDER BY dm.updated_at DESC, si.created_at DESC
                    LIMIT 1
                    """
                )
                result = cur.fetchone()
                
                if result:
                    TARGET_SERIAL = result['serial_number']
                    UPDATED_AT = result['updated_at']
                    ip_data = result['ip_address']

                    # ✅ FIXED: Extract IP from ip_address field with deterministic priority.
                    # Old code used dict iteration order which is random → caused wrong IP picked.
                    # New code: prefer wired (eth) → wlan → any other non-loopback interface.
                    # ip_data can be:
                    #   dict: {"eth0": "192.168.1.44", "lo": "127.0.0.1", "wlan0": "192.168.1.x"}
                    #   str:  "192.168.1.44"

                    if isinstance(ip_data, dict):
                        # Priority 1: eth interfaces (wired, most reliable)
                        for key, value in sorted(ip_data.items()):
                            if key.startswith('eth') and isinstance(value, str) \
                                    and value.count('.') == 3 \
                                    and not value.startswith('127.') \
                                    and value != '0.0.0.0':
                                TARGET_IP = value
                                break

                        # Priority 2: wlan interfaces
                        if not TARGET_IP:
                            for key, value in sorted(ip_data.items()):
                                if key.startswith('wlan') and isinstance(value, str) \
                                        and value.count('.') == 3 \
                                        and not value.startswith('127.') \
                                        and value != '0.0.0.0':
                                    TARGET_IP = value
                                    break

                        # Priority 3: any non-loopback IP
                        if not TARGET_IP:
                            for key, value in sorted(ip_data.items()):
                                if isinstance(value, str) \
                                        and value.count('.') == 3 \
                                        and not value.startswith('127.') \
                                        and value != '0.0.0.0':
                                    TARGET_IP = value
                                    break

                    elif isinstance(ip_data, str):
                        if not ip_data.startswith('127.') and ip_data != '0.0.0.0':
                            TARGET_IP = ip_data

                    print(f"\n{'='*80}")
                    print(f"🎯 LATEST UPDATED DEVICE:")
                    print(f"   Serial:    {TARGET_SERIAL}")
                    print(f"   Updated:   {UPDATED_AT}")
                    print(f"   IP data:   {ip_data}")
                    print(f"   Target IP: {TARGET_IP}")
                    print(f"{'='*80}")
                
    except Exception as e:
        print(f"❌ Database error: {e}")
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "message": "Database query failed",
            "error": str(e)
        }), 500
    
    if not TARGET_IP or not TARGET_SERIAL:
        return jsonify({
            "ok": False,
            "message": "No valid target device found"
        }), 404

    print(f"\n{'='*80}")
    print(f"📡 HEARTBEAT RECEIVED")
    print(f"   Sender: {agent_ip}")
    print(f"   Target: {TARGET_IP} (last updated)")
    print(f"{'='*80}")

    # ⛔ IF NOT TARGET IP, REJECT
    if agent_ip != TARGET_IP:
        print(f"   ❌ REJECTED: {agent_ip} != {TARGET_IP}")
        print(f"{'='*80}\n")
        return jsonify({
            "ok": False,
            "message": f"Only accepting from {TARGET_IP}",
            "target_ip": TARGET_IP,
            "target_serial": TARGET_SERIAL,
            "received_ip": agent_ip
        }), 403

    print(f"   ✅ ACCEPTED - Device discovery ENABLED")
    
    # ✅ FIXED: Use IP only as key (no port)
    key = agent_ip
    print(key,"---------------------------")
    snapshot_dir = get_snapshot_dir(TARGET_IP)

    with AGENTS_LOCK:
        AGENTS[key] = {
            "agent_ip": agent_ip,
            "agent_scheme": agent_scheme,
            "agent_port": agent_port,
            "last_seen_ts": time.time(),
            "last_seen": now_str(),
            "status": "ACTIVE",
            "snapshot_dir": snapshot_dir,
            "info": payload,
            "serial_number": TARGET_SERIAL,
            "device_updated_at": str(UPDATED_AT),
            "verified_at": datetime.now(timezone.utc).isoformat()
        }

    print(f"   ✅ Registered: {key}")
    print(f"{'='*80}\n")

    return jsonify({
        "ok": True,
        "message": "Agent registered - Device discovery enabled",
        "agent": key,
        "target_verified": True,
        "target_serial": TARGET_SERIAL,
        "snapshot_dir": snapshot_dir,
        "device_discovery_enabled": True
    })


# @app.route("/device_discovery", methods=["POST"])
# def device_discovery():
#     """
#     Client sends heartbeat:
#       POST https://SERVER_IP:5000/device_discovery
#       Content-Type: application/json
#       body: { IP, Hostname, agent_scheme, agent_port, ... }

#     IMPORTANT: silent=True prevents Flask auto 400 crash on invalid JSON.
#     """
#     payload = request.get_json(silent=True)
#     print(payload,"----------------------")

#     if payload is None:
#         raw = request.get_data(as_text=True)
#         return jsonify({
#             "ok": False,
#             "message": "Invalid/missing JSON. Send Content-Type: application/json",
#             "content_type": request.content_type,
#             "raw_preview": (raw[:500] if raw else "")
#         }), 400

#     agent_ip = payload.get("IP") or payload.get("ip") or get_remote_ip()
#     agent_scheme = (payload.get("agent_scheme") or DEFAULT_AGENT_SCHEME).strip().lower()
#     agent_port = int(payload.get("agent_port") or DEFAULT_AGENT_PORT)

#     key = f"{agent_ip}:{agent_port}"

#     with AGENTS_LOCK:
#         AGENTS[key] = {
#             "agent_ip": agent_ip,
#             "agent_scheme": agent_scheme,
#             "agent_port": agent_port,
#             "last_seen_ts": time.time(),
#             "last_seen": now_str(),
#             "status": "ACTIVE",
#             "info": payload
#         }

#     return jsonify({"ok": True, "message": "agent updated", "agent": key})
def _normalize_agent_id(agent_id: str) -> str:
    """
    Normalize agent_id to just IP address (remove port if present)
    Example: "192.168.1.111:5001" -> "192.168.1.111"
    """
    print('lllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllll')
    agent_id = (agent_id or "").strip()
    print(agent_id,"_____________________________")
    # If it's in format "IP:PORT", extract just the IP
    if ":" in agent_id and agent_id.count(".") == 3:
        # Check if it's an IP:PORT format
        parts = agent_id.split(":")
        if len(parts) == 2:
            ip_part = parts[0]
            # Validate it looks like an IP
            if all(part.isdigit() for part in ip_part.split(".") if part.isdigit()):
                return ip_part
    
    return agent_id

# ================================
# DEBUG: LIST AGENTS
# ================================
# ✅ FIX: Removed duplicate AGENTS_LOCK/AGENTS={} that was here.
# The duplicate reset was wiping AGENTS clean (line ~7017 ran AFTER
# device_discovery was defined at line ~6926). So every time api_scan
# checked AGENTS it was empty → fell through to socket hub → picked
# wrong agent (192.168.1.111) instead of the device_discovery-verified
# target (192.168.1.44). Single declaration is at line 6734/6735 above.
@app.route("/api/agents", methods=["GET"])
def api_agents():
    with AGENTS_LOCK:
        agents_list = list(AGENTS.values())
    agents_list.sort(key=lambda x: x.get("last_seen_ts", 0), reverse=True)
    return jsonify({"ok": True, "count": len(agents_list), "agents": agents_list})

from urllib.parse import quote

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """
    UI calls this to trigger ONVIF network scan on a connected agent.

    ✅ FIXED Agent selection priority:
      1. agent_id/agent_ip provided in request body → use that directly
      2. AGENTS dict (device_discovery verified target, PRIMARY) → use TARGET IP
      3. Socket hub _SOCKET_AGENT_CONNS (SECONDARY, fallback only)
      4. No agent found → 503

    ⚠️  ROOT CAUSE OF BUG (now fixed):
         Old code: picked socket_agents[0] (first socket-connected agent, arbitrary order).
         This completely ignored which device was verified by /device_discovery.
         Result: scan ran on 192.168.1.111 even though target was 192.168.1.44.

         New code: checks AGENTS dict FIRST (only the device_discovery-verified TARGET
         is stored there). If that agent is also in socket hub → use it.
         Only falls back to raw socket hub if AGENTS dict is empty.
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    customer_id = data.get("customer_id")

    print(f"\n{'='*70}")
    print(f"📡 SCAN REQUEST")
    print(f"   Username: {username}")
    print(f"   Password: {'*' * len(password)}")
    print(f"   Customer ID: {customer_id}")
    print(f"   Customer ID type: {type(customer_id).__name__}")
    print(f"   Raw request body: {data}")
    print(f"{'='*70}")

    if not username or not password:
        return jsonify({"status": "error", "message": "username/password required"}), 400

    # ── Step 1: Get agent_id from request (optional) ──────────────────────
    raw_agent_id = (data.get("agent_id") or data.get("agent") or "").strip()
    agent_id = _normalize_agent_id(raw_agent_id) if raw_agent_id else ""
    print(f"   Requested agent_id: '{agent_id}'")

    # ── Step 2: Auto-select agent if not provided ─────────────────────────
    # if not agent_id:
    #     print(f"   No agent_id in request — auto-selecting...")

    #     # ✅ FIX: PRIMARY = AGENTS dict (device_discovery verified target IP)
    #     # /device_discovery route only adds a device to AGENTS after confirming
    #     # it matches the last-updated TARGET_IP from device_master DB.
    #     # So AGENTS always contains exactly the right device to scan from.
    #     with AGENTS_LOCK:
    #         # Pick the most-recently seen verified agent
    #         verified_agents = sorted(
    #             AGENTS.items(),
    #             key=lambda kv: kv[1].get("last_seen_ts", 0),
    #             reverse=True
    #         )
    #         agents_keys = [k for k, _ in verified_agents]

    #     with _SOCKET_AGENT_LOCK:
    #         socket_agents = list(_SOCKET_AGENT_CONNS.keys())

    #     print(f"   AGENTS (device_discovery verified): {agents_keys}")
    #     print(f"   All socket hub agents: {socket_agents}")

    #     if agents_keys:
    #         # Use the most-recently verified target agent
    #         candidate = agents_keys[0]
    #         # Must also be connected on socket hub to receive commands
    #         if candidate in socket_agents:
    #             agent_id = candidate
    #             print(f"   ✅ Auto-selected VERIFIED target from AGENTS dict: {agent_id}")
    #         else:
    #             # Target device is registered but socket not connected yet —
    #             # warn clearly instead of silently using a wrong device
    #             print(f"   ⚠️  Verified target {candidate} not in socket hub!")
    #             print(f"   ⚠️  Socket hub has: {socket_agents}")
    #             # Try other verified agents that ARE in socket hub
    #             for k in agents_keys[1:]:
    #                 if k in socket_agents:
    #                     agent_id = k
    #                     print(f"   ✅ Using next verified agent in socket hub: {agent_id}")
    #                     break
    #             if not agent_id:
    #                 print(f"{'='*70}\n")
    #                 return jsonify({
    #                     "status": "error",
    #                     "message": (
    #                         f"Verified target {candidate} is not connected to socket hub. "
    #                         f"Ensure clienttt.py is running on {candidate}."
    #                     ),
    #                     "verified_target": candidate,
    #                     "socket_hub_agents": socket_agents,
    #                     "hint": f"Restart clienttt.py on {candidate} and wait for socket connection."
    #                 }), 503
    #     elif socket_agents:
    #         # ✅ SECONDARY fallback: no device_discovery verified agent yet,
    #         # use whatever is in socket hub (same as old behaviour, but only as fallback)
    #         agent_id = socket_agents[0]
    #         print(f"   ⚠️  No verified agent — falling back to socket hub: {agent_id}")
    #         print(f"   ⚠️  WARNING: This may not be the correct target device!")
    #         print(f"   ⚠️  Ensure /device_discovery heartbeat is running on the target device.")
    #     else:
    #         print(f"   ❌ NO AGENTS FOUND ANYWHERE!")
    #         print(f"   AGENTS (verified): empty")
    #         print(f"   Socket hub agents: empty")
    #         print(f"{'='*70}\n")
    #         return jsonify({
    #             "status": "error",
    #             "message": "No agents connected. Ensure clienttt.py is running and connected.",
    #             "details": {
    #                 "socket_hub_agents": 0,
    #                 "verified_agents": 0,
    #                 "socket_hub_port": SOCKET_HUB_PORT,
    #             },
    #             "troubleshooting": [
    #                 "1. Check agent is running: ps aux | grep clienttt.py",
    #                 "2. Check agent logs for: 'Connected to socket hub'",
    #                 f"3. Verify agent can reach server port {SOCKET_HUB_PORT}",
    #                 "4. Restart agent: python3 clienttt.py",
    #                 f"5. Check connected agents: GET /api/agents or /socket-hub/agents"
    #             ]
    #         }), 503

    # ── Step 2: Auto-select VERIFIED agent from AGENTS dict ─────────────────
    if not agent_id:
        print("   No agent_id in request — selecting verified target from AGENTS...")

        with AGENTS_LOCK:
            if AGENTS:
                # ✅ Pick most recently seen verified agent
                sorted_agents = sorted(
                    AGENTS.items(),
                    key=lambda kv: kv[1].get("last_seen_ts", 0),
                    reverse=True
                )
                agent_id = sorted_agents[0][0]
                print(f"   ✅ Using verified target IP from AGENTS: {agent_id}")
            else:
                print("   ⚠️  No verified agents in AGENTS dict — falling back to socket hub")

        # Fallback: use socket hub agent if AGENTS dict empty
        if not agent_id:
            with _SOCKET_AGENT_LOCK:
                socket_agents = list(_SOCKET_AGENT_CONNS.keys())
            if socket_agents:
                agent_id = socket_agents[0]
                print(f"   ⚠️  Fallback: using socket hub agent: {agent_id}")
            else:
                print(f"   ❌ No agents anywhere!")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "error",
                    "message": "No agents connected. Ensure agent is running and connected."
                }), 503

    # ── Step 3: Validate agent_id is in socket hub ───────────────────────
    # If a specific agent_id was provided in request but is not in socket hub,
    # return a clear error (do NOT silently switch to a different wrong device)
    with _SOCKET_AGENT_LOCK:
        is_in_socket_hub = agent_id in _SOCKET_AGENT_CONNS
        all_socket_agents = list(_SOCKET_AGENT_CONNS.keys())

    if not is_in_socket_hub:
        print(f"   ⚠️  Requested agent '{agent_id}' NOT in socket hub")
        print(f"   Socket hub has: {all_socket_agents}")
        print(f"{'='*70}\n")
        return jsonify({
            "status": "error",
            "message": f"Target agent {agent_id} is not connected to socket hub.",
            "requested_agent": agent_id,
            "socket_hub_agents": all_socket_agents,
            "hint": f"Restart clienttt.py on {agent_id} and wait for socket connection."
        }), 503

    print(f"   ✅ Final agent_id: {agent_id}")

    # ── Step 4: Send scan command via socket hub ──────────────────────────
    scan_data = {"username": username, "password": password, "customer_id": customer_id}
    print(f"\n🔌 Sending scan command via socket hub to agent: {agent_id}")
    print(f"   📤 Data being sent to Pi: {scan_data}")
    print(f"   📤 customer_id value: {customer_id} (type: {type(customer_id).__name__})")

    sock_resp = socket_hub_send_command(
        agent_id=agent_id,
        command="scan",
        data=scan_data,
        timeout=600
    )

    print(f"   📥 Socket response: {sock_resp}")

    # ── Handle async scan acknowledgment (scan_started: True) ────────────
    if sock_resp and sock_resp.get("status") == "ok":
        payload = sock_resp.get("payload") or sock_resp.get("data") or {}

        if isinstance(payload, dict) and payload.get("scan_started") is True:
            print(f"   ⏳ Scan started asynchronously — polling for results...")

            scan_results = _wait_for_scan_results(
                agent_id=agent_id,
                username=username,
                password=password,
                max_wait_seconds=600,
                poll_interval=2
            )

            if scan_results:
                print(f"   ✅ SCAN SUCCESS! Found {scan_results.get('count', 0)} devices")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "ok",
                    "count": scan_results.get("count", 0),
                    "devices": scan_results.get("devices", []),
                    "source": "socket_hub_async"
                }), 200
            else:
                print(f"   ⏱️ Scan results timeout")
                print(f"{'='*70}\n")
                return jsonify({
                    "status": "error",
                    "message": "Scan timeout — agent is still scanning or no cameras found.",
                    "hint": "Scan may still be running. Try again in a few moments.",
                    "source": "socket_hub_async"
                }), 504

        # Sync response with devices in payload
        if isinstance(payload, dict) and "devices" in payload:
            print(f"   ✅ SCAN SUCCESS (sync)! Found {payload.get('count', 0)} devices")
            print(f"{'='*70}\n")
            return jsonify({
                "status": "ok",
                "count": payload.get("count", 0),
                "devices": payload.get("devices", []),
                "source": "socket_hub_sync"
            }), 200

        # Sync response devices at top level
        if "count" in sock_resp and "devices" in sock_resp:
            print(f"   ✅ SCAN SUCCESS (sync)! Found {sock_resp.get('count', 0)} devices")
            print(f"{'='*70}\n")
            return jsonify({
                "status": "ok",
                "count": sock_resp.get("count", 0),
                "devices": sock_resp.get("devices", []),
                "source": "socket_hub_sync"
            }), 200

    # ── Handle error response from agent ─────────────────────────────────
    if sock_resp and sock_resp.get("status") == "error":
        error_msg = sock_resp.get("message", "Unknown error")
        print(f"   ❌ AGENT RETURNED ERROR: {error_msg}")
        print(f"{'='*70}\n")

        if "timeout" in error_msg.lower():
            return jsonify({
                "status": "error",
                "message": "Scan timeout — agent is busy or scanning a large network.",
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

    # ── No response at all ────────────────────────────────────────────────
    print(f"   ❌ No response from agent (timeout or disconnected)")
    print(f"{'='*70}\n")
    return jsonify({
        "status": "error",
        "message": "Agent did not respond to scan command (socket timeout).",
        "agent_id": agent_id,
        "hint": "Agent may have disconnected. Check agent logs and restart if needed.",
        "source": "socket_hub"
    }), 503


# ✅ NEW: Global dict to store async scan results
# Key: f"{agent_id}:{username}:{password_hash}" -> {"count": N, "devices": [...]}
SCAN_RESULTS_LOCK = threading.Lock()
SCAN_RESULTS = {}  # scan_key -> {"count": N, "devices": [...], "timestamp": T}

def _generate_scan_key(agent_id: str, username: str, password: str) -> str:
    """Generate unique key for scan request to match with async results."""
    import hashlib
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()[:16]
    return f"{agent_id}:{username}:{pwd_hash}"

def _wait_for_scan_results(agent_id: str, username: str, password: str, max_wait_seconds: int = 40, poll_interval: int = 2) -> dict:
    """
    Poll for scan results that will be posted by agent via callback endpoint.
    
    Args:
        agent_id: Agent IP address
        username: ONVIF username used for scan
        password: ONVIF password used for scan
        max_wait_seconds: Maximum time to wait for results (default 600s = 10 min)
        poll_interval: How often to check for results (default 2s)
    
    Returns:
        dict with 'count' and 'devices' keys, or None if timeout
    """
    scan_key = _generate_scan_key(agent_id, username, password)
    start_time = time.time()
    attempts = 0
    
    print(f"   🔍 Polling for scan results with key: {scan_key}")
    
    while (time.time() - start_time) < max_wait_seconds:
        attempts += 1
        
        with SCAN_RESULTS_LOCK:
            if scan_key in SCAN_RESULTS:
                results = SCAN_RESULTS.pop(scan_key)  # Remove after reading
                elapsed = time.time() - start_time
                print(f"   ✅ Scan results received after {elapsed:.1f}s (attempt #{attempts})")
                return results
        
        # Log progress every 10 seconds
        if attempts % 5 == 0:  # Every 5 attempts (10 seconds if poll_interval=2)
            elapsed = time.time() - start_time
            print(f"   ⏳ Still waiting for scan results... {elapsed:.0f}s elapsed")
        
        time.sleep(poll_interval)
    
    # Timeout
    elapsed = time.time() - start_time
    print(f"   ⏱️ Scan results polling timeout after {elapsed:.1f}s ({attempts} attempts)")
    return None


@app.route("/api/scan_results", methods=["POST"])
def api_scan_results():
    """
    ✅ NEW ENDPOINT: Agent POSTs scan results here after completing async scan.
    
    Expected payload:
    {
        "agent_id": "192.168.1.111",
        "username": "admin",
        "password_hash": "abc123...",  # First 16 chars of SHA256
        "count": 5,
        "devices": [...]
    }
    """
    data = request.get_json(silent=True) or {}
    
    agent_id = (data.get("agent_id") or "").strip()
    username = (data.get("username") or "").strip()
    password_hash = (data.get("password_hash") or "").strip()
    count = data.get("count", 0)
    devices = data.get("devices", [])
    user_id_from_pi = data.get("user_id")

    print(f"\n{'='*70}")
    print(f"📥 SCAN RESULTS RECEIVED FROM PI")
    print(f"   Agent ID: {agent_id}")
    print(f"   Username: {username}")
    print(f"   Password Hash: {password_hash}")
    print(f"   Count: {count}")
    print(f"   📌 user_id (top-level from Pi): {user_id_from_pi}")
    print(f"   📌 user_id type: {type(user_id_from_pi).__name__}")
    if devices:
        for i, dev in enumerate(devices[:3]):
            print(f"   📌 Device[{i}] user_id: {dev.get('user_id', 'NOT SET')} | ip: {dev.get('ip', dev.get('ip_address', 'N/A'))}")
        if len(devices) > 3:
            print(f"   ... and {len(devices) - 3} more devices")
    print(f"   📌 Full payload keys: {list(data.keys())}")
    print(f"{'='*70}")
    
    if not agent_id or not username or not password_hash:
        print(f"   ❌ Missing required fields")
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
    
    print(f"   ✅ Scan results stored with key: {scan_key}")
    print(f"{'='*70}\n")
    
    return jsonify({
        "status": "ok",
        "message": "Scan results received and stored"
    }), 200


# ✅ NEW: Cleanup old scan results (prevent memory leak)
def _cleanup_old_scan_results():
    """Remove scan results older than 10 minutes."""
    while True:
        try:
            time.sleep(60)  # Run every minute
            
            with SCAN_RESULTS_LOCK:
                now = time.time()
                expired_keys = [
                    key for key, value in SCAN_RESULTS.items()
                    if (now - value.get("timestamp", 0)) > 600  # 10 minutes
                ]
                
                for key in expired_keys:
                    del SCAN_RESULTS[key]
                
                if expired_keys:
                    print(f"🧹 Cleaned up {len(expired_keys)} expired scan results")
        
        except Exception as e:
            print(f"⚠️ Error in scan results cleanup: {e}")


# ✅ Start cleanup thread when server starts
threading.Thread(target=_cleanup_old_scan_results, daemon=True).start()

# Snapshot storage

# SNAPSHOT_DIR = r"\\192.168.1.111\SharedFolder"
SNAPSHOT_DIR=r"\\192.168.1.111\pi4\SharedFolder"
@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename):
    full_path = os.path.join(SNAPSHOT_DIR, filename)
    print(full_path,"====000000000000000000000")

    print("==== SNAPSHOT SERVE DEBUG ====")
    print("Requested filename:", filename)
    print("SNAPSHOT_DIR:", SNAPSHOT_DIR)
    print("Full path:", full_path)
    print("Exists?:", os.path.exists(full_path))
    print("Is file?:", os.path.isfile(full_path))
    try:
        print("Dir listing (first 20):", os.listdir(SNAPSHOT_DIR)[:20])
    except Exception as e:
        print("Listdir error:", e)

    if not os.path.isfile(full_path):
        abort(404)

    return send_from_directory(SNAPSHOT_DIR, filename)

# ==============================
# BACKGROUND WATCHER (check inactive devices)
# ==============================
def inactive_watcher():
    while True:
        now = time.time()

        # Check if the device has been inactive for too long
        for host, d in list(DEVICES.items()):
            if now - d["last_seen_ts"] > HEARTBEAT_TIMEOUT:
                if d["status"] != "INACTIVE":
                    d["status"] = "INACTIVE"
                    d["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    write_status_to_db(d["info"], "INACTIVE")
                    update_device_status(d["info"], "INACTIVE")
        time.sleep(2)



# def mark_inactive_watcher():
#     while True:
#         try:
#             t = time.time()
#             with AGENTS_LOCK:
#                 for _, v in list(AGENTS.items()):
#                     if t - v.get("last_seen_ts", 0) > HEARTBEAT_TIMEOUT:
#                         v["status"] = "INACTIVE"
#         except Exception:
#             pass
#         time.sleep(2)


# threading.Thread(target=mark_inactive_watcher, daemon=True).start()


PG_SSLMODE = os.getenv("PG_SSLMODE", "prefer")  # disable/allow/prefer/require/verify-ca/verify-full


def _build_dsn() -> str:
    return (
        f"host={DB_CONFIG['host']} port={DB_CONFIG['port']} dbname={DB_CONFIG['dbname']} "
        f"user={DB_CONFIG['user']} password={DB_CONFIG['password']} sslmode={PG_SSLMODE}"
    )

# =============================================================================
# Robust JSON parsing (FIX for your 400 issue)
# =============================================================================
def _extract_first_ip(ip_dict_or_value) -> Optional[str]:
    """Extract a usable (non-loopback) IP from the "IP Address" payload.

    Your agents usually send "IP Address" as a dict like:
      {"lo": "127.0.0.1", "eth0": "192.168.1.111"}

    IMPORTANT FIX:
    - Earlier code used the *first* value, which often becomes 127.0.0.1 (lo).
      That makes *all devices look like the same IP*, so device_status inserts only once.

    This function:
    - Skips loopback (127.0.0.1, ::1), 0.0.0.0, link-local (169.254.x.x), etc.
    - Prefers real LAN/Wi-Fi interfaces first (eth*, en*, wlan*, wl*).
    """

    def _clean(v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        # drop IPv6 zone index (e.g. fe80::1%wlan0)
        if "%" in s:
            s = s.split("%", 1)[0].strip()
        return s

    def _is_good_ip(ip_str: str) -> bool:
        try:
            import ipaddress
            a = ipaddress.ip_address(ip_str)
            if a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified:
                return False
            if str(a) == "0.0.0.0":
                return False
            return True
        except Exception:
            return False

    if not ip_dict_or_value:
        return None

    # Dict case: {iface: ip}
    if isinstance(ip_dict_or_value, dict):
        items = []
        for k, v in ip_dict_or_value.items():
            ip = _clean(v)
            iface = str(k).strip() if k is not None else ""
            if ip:
                items.append((iface, ip))

        if not items:
            return None

        preferred_prefixes = ("eth", "en", "eno", "ens", "enp", "wlan", "wl")

        # 1) preferred interfaces with a good IP
        for pref in preferred_prefixes:
            for iface, ip in items:
                if iface.lower().startswith(pref) and _is_good_ip(ip):
                    return ip

        # 2) any non-loopback interface with a good IP
        for iface, ip in items:
            if iface.lower() in ("lo", "loopback"):
                continue
            if _is_good_ip(ip):
                return ip

        # 3) fallback: any non-loopback string (still avoid 127.* and ::1)
        for iface, ip in items:
            if iface.lower() in ("lo", "loopback"):
                continue
            if ip.startswith("127.") or ip == "::1":
                continue
            return ip

        return None

    # Single value case
    ip = _clean(ip_dict_or_value)
    if not ip:
        return None
    if ip.startswith("127.") or ip == "::1":
        return None
    return ip


def check_device_online(device_hostname: str = "", device_ip: str = "") -> Tuple[bool, str]:
    """
    Returns: (is_online, reason_message)

    Priority:
    1) In-memory DEVICES dict (fast)  [your heartbeat system]
    2) DB fallback: device_status (latest row)
    3) DB fallback: system_information.status
    """
    device_hostname = (device_hostname or "").strip()
    device_ip = (device_ip or "").strip()

    # -----------------------
    # 1) DEVICES dict check
    # -----------------------
    if "DEVICES" in globals():
        DEV = globals().get("DEVICES") or {}

        # Direct hostname match
        if device_hostname and device_hostname in DEV:
            d = DEV.get(device_hostname) or {}
            st = (d.get("status") or "").upper()
            if st in ("ACTIVE", "ONLINE"):
                return True, f"ONLINE via DEVICES (hostname={device_hostname}, status={st})"
            return False, f"OFFLINE via DEVICES (hostname={device_hostname}, status={st})"

        # If hostname not found, try match by IP inside DEVICES info
        if device_ip:
            for host, d in DEV.items():
                info = (d or {}).get("info") or {}
                ip_map = info.get("IP Address") or {}
                ip_found = _extract_first_ip(ip_map)
                if ip_found and ip_found == device_ip:
                    st = ((d or {}).get("status") or "").upper()
                    if st in ("ACTIVE", "ONLINE"):
                        return True, f"ONLINE via DEVICES (ip={device_ip}, hostname={host}, status={st})"
                    return False, f"OFFLINE via DEVICES (ip={device_ip}, hostname={host}, status={st})"

    # -----------------------
    # 2) DB fallback: device_status
    # -----------------------
    try:
        if device_ip:
            with psycopg2.connect(_build_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT status
                        FROM device_status
                        WHERE ip_address = %s
                        ORDER BY last_change_at DESC
                        LIMIT 1
                        """,
                        (device_ip,),
                    )
                    row = cur.fetchone()
                    if row:
                        st = (row[0] or "").upper()
                        if st in ("ONLINE", "ACTIVE"):
                            return True, f"ONLINE via DB device_status (ip={device_ip}, status={st})"
                        return False, f"OFFLINE via DB device_status (ip={device_ip}, status={st})"
    except Exception:
        pass

    # -----------------------
    # 3) DB fallback: system_information.status
    # -----------------------
    try:
        if device_hostname:
            with psycopg2.connect(_build_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT status
                        FROM system_information
                        WHERE hostname = %s
                        LIMIT 1
                        """,
                        (device_hostname,),
                    )
                    row = cur.fetchone()
                    if row:
                        st = (row[0] or "").upper()
                        if st in ("ACTIVE", "ONLINE"):
                            return True, f"ONLINE via DB system_information (hostname={device_hostname}, status={st})"
                        return False, f"OFFLINE via DB system_information (hostname={device_hostname}, status={st})"
    except Exception as e:
        return False, f"DB check failed while verifying device online: {str(e)}"

    return False, "Device not found in DEVICES or DB (cannot verify online status)."


# =============================================================================
# Postgres registry
# =============================================================================

class PGAssetRegistry:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def tables_exist(self) -> Tuple[bool, List[str]]:
        """
        Check required tables exist in public schema.
        NOTE: We only require public.assets for this simplified version.
        """
        missing = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.assets');")
                if cur.fetchone()[0] is None:
                    missing.append("public.assets")
        return (len(missing) == 0, missing)

    def check_db(self) -> Tuple[bool, str]:
        ok, missing = self.tables_exist()
        if not ok:
            return False, f"Required tables missing: {', '.join(missing)}"

        with self._connect() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM public.assets;")
                    _ = cur.fetchone()[0]
                conn.rollback()
                return True, "DB check OK: public.assets exists + read permissions OK."
            except Exception as e:
                conn.rollback()
                return False, f"DB check failed (permissions or connectivity issue): {str(e)}"

    # -------------------------------------------------------------------------
    # ADDED (without removing anything):
    # Fetch current qr_status for a serial (None if not exists)
    # -------------------------------------------------------------------------
    def get_qr_status(self, serial: str) -> Optional[str]:
        serial = (serial or "").strip()
        if not serial:
            return None
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT qr_status FROM public.assets WHERE serial = %s LIMIT 1;", (serial,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    return (row[0] or "").strip().upper()
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # ADDED (without removing anything):
    # Update qr_status for a serial list (bulk)
    # -------------------------------------------------------------------------
    def bulk_update_qr_status(self, serials: List[str], new_status: str) -> int:
        serials = [s.strip() for s in (serials or []) if str(s).strip()]
        new_status = (new_status or "").strip().upper()
        if not serials or not new_status:
            return 0

        with self._connect() as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.assets
                    SET qr_status = %s
                    WHERE serial = ANY(%s);
                    """,
                    (new_status, serials),
                )
                updated = cur.rowcount or 0
            conn.commit()
            return updated

    # -------------------------------------------------------------------------
    # ADDED (without removing anything):
    # This method guarantees "no duplicates" even WITHOUT a DB UNIQUE constraint.
    # Returns True if inserted, False if already exists.
    # -------------------------------------------------------------------------
    def insert_asset_no_duplicate(self, serial: str, qr_status: str = "PENDING") -> bool:
        serial = (serial or "").strip()
        qr_status = (qr_status or "PENDING").strip().upper()

        if not serial:
            raise ValueError("serial is required")

        with self._connect() as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                # Atomic insert-if-not-exists (works even without UNIQUE constraint)
                cur.execute(
                    """
                    INSERT INTO public.assets(serial, qr_status, created_at)
                    SELECT %s, %s, NOW()
                    WHERE NOT EXISTS (SELECT 1 FROM public.assets WHERE serial = %s);
                    """,
                    (serial, qr_status, serial),
                )
                inserted = (cur.rowcount == 1)
            conn.commit()
            return inserted

    def insert_asset(self, serial: str, qr_status: str = "PENDING") -> None:
        """
        Inserts ONLY:
          - serial
          - qr_status
          - created_at (NOW())

        IMPORTANT:
        - This version will NOT crash if serial is not UNIQUE in DB.
        - But duplicates can be inserted if you don't add UNIQUE constraint.
        """

        # ---------------------------------------------------------------------
        # MODIFIED (without removing anything):
        # We now call insert_asset_no_duplicate() to prevent repeated inserts.
        # The original ON CONFLICT block is kept below (not removed), but it is
        # not relied upon anymore because your DB may not have UNIQUE(serial).
        # ---------------------------------------------------------------------
        serial = (serial or "").strip()
        qr_status = (qr_status or "PENDING").strip().upper()

        if not serial:
            raise ValueError("serial is required")

        # Use safe no-duplicate insert
        _ = self.insert_asset_no_duplicate(serial=serial, qr_status=qr_status)

       

# =============================================================================
# TSPL label building (fixed layout)
# =============================================================================
def _tspl_escape(s: str) -> str:
    return (s or "").replace('"', "'").strip()


def build_tspl_label_fixed_layout(code_value: str) -> bytes:
    code = _tspl_escape(code_value)

    tspl = f"""
SIZE 60 mm, 30.1 mm
GAP 3 mm, 0 mm
DIRECTION 0,0
REFERENCE 0,0
OFFSET 0 mm
SET PEEL OFF
SET CUTTER OFF
SET PARTIAL_CUTTER OFF
SET TEAR ON
CLS
CODEPAGE 1252
BARCODE 379,280,"128M",150,0,180,4,8,"!105{code}"
TEXT 321,121,"ROMAN.TTF",180,1,12,"{code}"
QRCODE 600,256,L,6,A,180,M2,S7,"{code}"
TEXT 637,103,"ROMAN.TTF",180,1,12,"{code}"
PRINT 1,1
"""
    return tspl.strip().encode("ascii", errors="replace")


# =============================================================================
# Windows RAW printing
# =============================================================================
def list_windows_printers() -> List[str]:
    try:
        import win32print
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = win32print.EnumPrinters(flags)
    return [p[2] for p in printers]


def send_raw_to_windows_printer(printer_name: str, raw_bytes: bytes) -> None:
    import win32print
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        _ = win32print.StartDocPrinter(hPrinter, 1, ("TSC QR Labels", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, raw_bytes)
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)


def send_raw_to_network_printer(ip: str, port: int, raw_bytes: bytes, timeout_sec: float = 5.0) -> None:
    with socket.create_connection((ip, port), timeout=timeout_sec) as s:
        s.sendall(raw_bytes)


# =============================================================================
# Helpers (CSV + rows)
# =============================================================================
def load_csv_rows_from_file_storage(file_storage) -> List[Dict[str, str]]:
    content = file_storage.read()
    try:
        text = content.decode("utf-8-sig", errors="replace")
    except Exception:
        text = content.decode("utf-8", errors="replace")

    rows: List[Dict[str, str]] = []
    reader = csv.DictReader(text.splitlines())
    for r in reader:
        clean: Dict[str, str] = {}
        for k, v in (r or {}).items():
            kk = (k or "").strip()
            if not kk:
                continue
            vv = v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())
            clean[kk] = vv
        rows.append(clean)
    return rows


def normalize_rows(rows: List[Dict]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        clean: Dict[str, str] = {}
        for k, v in r.items():
            kk = str(k).strip()
            if not kk:
                continue
            clean[kk] = (str(v).strip() if v is not None else "")
        out.append(clean)
    return out


def _rows_from_request_or_payload(payload: Optional[dict]) -> List[Dict[str, str]]:
    """
    FIX: accept BOTH formats
      A) {"rows":[{"serial":"..."}]}
      B) {"serial":"..."}  <-- your dashboard sends this today
    """
    rows: List[Dict[str, str]] = []

    # JSON rows
    if payload and isinstance(payload.get("rows"), list):
        rows = normalize_rows(payload.get("rows"))

    # CSV upload
    if "csv_file" in request.files and request.files["csv_file"].filename:
        rows = normalize_rows(load_csv_rows_from_file_storage(request.files["csv_file"]))

    # Backward-compat: single serial
    if not rows and payload and isinstance(payload, dict):
        single_serial = (payload.get("serial") or payload.get("serial_no") or payload.get("sn") or "").strip()
        if single_serial:
            rows = [{"serial": single_serial}]

    # Also allow form field "serial" if someone uses x-www-form-urlencoded
    if not rows:
        single_serial_form = (request.form.get("serial") or request.form.get("serial_no") or request.form.get("sn") or "").strip()
        if single_serial_form:
            rows = [{"serial": single_serial_form}]

    return rows


# =============================================================================
# API ROUTES
# =============================================================================
@app.route("/api/assets/health", methods=["GET"])
def assets_health():
    return jsonify({
        "status": "success",
        "message": "Asset Label API is running",
        "server_time": datetime.now().isoformat()
    }), 200


@app.route("/api/assets/list-printers", methods=["GET"])
def api_list_printers():
    try:
        printers = list_windows_printers()
        if not printers:
            return jsonify({
                "status": "error",
                "message": "No printers found OR pywin32 missing. Install: pip install pywin32",
                "printers": []
            }), 200
        return jsonify({"status": "success", "printers": printers}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "Failed to list printers", "error": str(e)}), 500


@app.route("/api/assets/check-db", methods=["GET"])
def api_check_db():
    try:
        registry = PGAssetRegistry(_build_dsn())
        ok, msg = registry.check_db()
        return jsonify({
            "status": "success" if ok else "error",
            "ok": ok,
            "message": msg
        }), 200 if ok else 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "DB check failed", "error": str(e)}), 500


@app.route("/api/assets/generate-and-print", methods=["POST"])
def api_generate_and_print():
    """
    FIXED:
    - accepts rows[] OR csv_file OR single "serial"
    - accepts print.mode "preview" as alias of "none"
    - AFTER SUCCESSFUL PRINT: qr_status -> PRINTED (DB update)
    - IF ALREADY PRINTED: return "Already generated" (block)
    """
    try:
        payload = request.get_json(silent=True) if request.is_json else None

        qr_template = (request.form.get("qr_template") or (payload or {}).get("qr_template") or "{serial}").strip()

        dry_run_val = request.form.get("dry_run") if not payload else (payload or {}).get("dry_run")
        dry_run = str(dry_run_val).strip().lower() in ("1", "true", "yes", "y", "on")

        return_tspl_val = request.form.get("return_tspl") if not payload else (payload or {}).get("return_tspl")
        return_tspl = str(return_tspl_val).strip().lower() in ("1", "true", "yes", "y", "on")

        # rows from JSON / CSV / single serial
        rows = _rows_from_request_or_payload(payload)

        if not rows:
            return jsonify({
                "status": "error",
                "message": "Provide rows[] in JSON OR upload csv_file with serial/serial_no/sn column.",
                "debug": {
                    "content_type": request.content_type,
                    "is_json": request.is_json,
                    "form_keys": list(request.form.keys()),
                    "file_keys": list(request.files.keys()),
                    "json_keys": list((payload or {}).keys()) if isinstance(payload, dict) else None,
                }
            }), 400

        # print config
        if payload and isinstance(payload.get("print"), dict):
            print_mode = (payload["print"].get("mode") or "none").strip().lower()
            printer_name = (payload["print"].get("printer") or "").strip()
            net_ip = (payload["print"].get("net_ip") or "").strip()
            net_port = int(payload["print"].get("net_port") or 9100)
        else:
            print_mode = (request.form.get("print_mode") or "none").strip().lower()
            printer_name = (request.form.get("printer") or "").strip()
            net_ip = (request.form.get("net_ip") or "").strip()
            net_port = int(request.form.get("net_port") or 9100)

        # FIX: allow dashboard "preview"
        if print_mode == "preview":
            print_mode = "none"
            dry_run = True

        if print_mode not in ("none", "windows", "network"):
            return jsonify({"status": "error", "message": "print.mode must be one of: none, windows, network"}), 400

        # device online gate
        device_hostname = (request.form.get("device_hostname") or (payload or {}).get("device_hostname") or "").strip()
        device_ip = (request.form.get("device_ip") or (payload or {}).get("device_ip") or "").strip()
        on_offline = (request.form.get("on_offline") or (payload or {}).get("on_offline") or "deny").strip().lower()
        if on_offline not in ("deny", "skip"):
            on_offline = "deny"

        must_check_online = (not dry_run) and (print_mode in ("windows", "network"))

        # ---------------------------------------------------------------------
        # MODIFIED (without removing anything):
        # Earlier: if must_check_online and no device_hostname/device_ip -> 400.
        # Now: we only enforce online check if user PROVIDED hostname or ip.
        # If not provided, we SKIP this gate (no error).
        # ---------------------------------------------------------------------
        device_gate_applied = False
        device_online = None
        device_reason = None

        if must_check_online and (device_hostname or device_ip):
            device_gate_applied = True
            is_on, reason = check_device_online(device_hostname=device_hostname, device_ip=device_ip)
            device_online = is_on
            device_reason = reason
            if (not is_on) and on_offline == "deny":
                return jsonify({
                    "status": "error",
                    "message": "Device is OFFLINE. Printing blocked.",
                    "device_online": False,
                    "reason": reason
                }), 409

        # DB tables must exist
        registry = PGAssetRegistry(_build_dsn())
        ok, missing = registry.tables_exist()
        if not ok:
            return jsonify({
                "status": "error",
                "message": "Required tables missing in DB.",
                "missing": missing
            }), 500

        assets_out = []
        tspl_list: List[str] = []
        raw_bytes_list: List[bytes] = []

        # track inserted vs skipped
        inserted_serials: List[str] = []
        skipped_serials: List[str] = []

        # ADDED: already printed block list
        already_printed_serials: List[str] = []

        # ADDED: serials that are actually going to be printed in this job
        will_print_serials: List[str] = []

        for i, row in enumerate(rows):
            serial = (row.get("serial") or row.get("serial_no") or row.get("sn") or "").strip()
            if not serial:
                return jsonify({
                    "status": "error",
                    "message": "serial is REQUIRED for every row (serial/serial_no/sn).",
                    "bad_row_index": i,
                    "bad_row": row
                }), 400

            qr_status = (row.get("qr_status") or "PENDING").strip().upper()

            # -----------------------------------------------------------------
            # ADDED: If DB already has PRINTED => block this serial
            # -----------------------------------------------------------------
            existing_status = registry.get_qr_status(serial)
            if existing_status == "PRINTED":
                already_printed_serials.append(serial)
                assets_out.append({
                    "serial": serial,
                    "qr_status": existing_status,
                    "inserted": False,
                    "blocked": True,
                    "message": "Already generated/printed"
                })
                # Do NOT add TSPL for this serial
                continue

            # insert (no duplicates)
            inserted = registry.insert_asset_no_duplicate(serial=serial, qr_status=qr_status)
            if inserted:
                inserted_serials.append(serial)
            else:
                skipped_serials.append(serial)

            # keep existing call too (not removed) - but safe now:
            registry.insert_asset(serial=serial, qr_status=qr_status)

            # build label data (only for allowed serials)
            qr_payload = qr_template.format(serial=serial)
            tspl_bytes = build_tspl_label_fixed_layout(qr_payload)
            raw_bytes_list.append(tspl_bytes)
            will_print_serials.append(serial)

            if return_tspl:
                tspl_list.append(tspl_bytes.decode("ascii", errors="replace"))

            assets_out.append({
                "serial": serial,
                "qr_status": qr_status,
                "inserted": inserted,
                "blocked": False
            })

        # If nothing to print because all were already PRINTED
        if (not dry_run) and (print_mode in ("windows", "network")) and (len(raw_bytes_list) == 0) and already_printed_serials:
            return jsonify({
                "status": "error",
                "message": "Already generated/printed. No new labels to print.",
                "already_printed_serials": already_printed_serials,
                "count": len(assets_out),
                "assets": assets_out
            }), 409

        job_bytes = b"".join(raw_bytes_list)

        printed = False
        print_message = "Print skipped (mode=none)"

        # If printing requested, re-check online (skip mode)
        # NOTE: preserved, but made safe (won't block if no device info)
        if not dry_run and print_mode in ("windows", "network") and (device_hostname or device_ip):
            is_on, reason = check_device_online(device_hostname=device_hostname, device_ip=device_ip)
            if not is_on:
                printed = False
                print_message = f"Device OFFLINE => print skipped. ({reason})"
                return jsonify({
                    "status": "success",
                    "message": "Serials processed, but printing skipped due to offline device.",
                    "device_gate_applied": device_gate_applied,
                    "device_online": False,
                    "reason": reason,
                    "count": len(assets_out),
                    "printed": printed,
                    "print_message": print_message,
                    "inserted_serials": inserted_serials,
                    "skipped_serials": skipped_serials,
                    "already_printed_serials": already_printed_serials,
                    "assets": assets_out,
                    "tspl_preview": tspl_list if return_tspl else None
                }), 200

        # Normal print flow
        if dry_run:
            printed = False
            print_message = "Dry-run enabled: TSPL generated but not printed."
        else:
            if print_mode == "network":
                if not net_ip:
                    return jsonify({"status": "error", "message": "net_ip is required when print.mode=network"}), 400
                if job_bytes:
                    send_raw_to_network_printer(net_ip, net_port, job_bytes)
                    printed = True
                    print_message = f"Printed via network to {net_ip}:{net_port}"
                else:
                    printed = False
                    print_message = "No new labels to print (already printed items were skipped)."

            elif print_mode == "windows":
                if not printer_name:
                    return jsonify({"status": "error", "message": 'printer is required when print.mode=windows'}), 400
                if job_bytes:
                    send_raw_to_windows_printer(printer_name, job_bytes)
                    printed = True
                    print_message = f'Printed to Windows printer: "{printer_name}"'
                else:
                    printed = False
                    print_message = "No new labels to print (already printed items were skipped)."

        # ---------------------------------------------------------------------
        # ADDED: After SUCCESSFUL PRINT => update DB qr_status to PRINTED
        # Only for serials that were actually printed in this job.
        # ---------------------------------------------------------------------
        updated_to_printed = 0
        if printed and (not dry_run) and will_print_serials:
            try:
                updated_to_printed = registry.bulk_update_qr_status(will_print_serials, "PRINTED")
                # also update response objects to show PRINTED
                for a in assets_out:
                    if (a.get("serial") in will_print_serials) and (not a.get("blocked")):
                        a["qr_status"] = "PRINTED"
            except Exception:
                # If DB update fails, still don't break printing success
                traceback.print_exc()

        return jsonify({
            "status": "success",
            "message": "Serials processed successfully.",
            "device_gate_applied": device_gate_applied,
            "device_online": True if device_gate_applied and device_online else (None if not device_gate_applied else False),
            "device_reason": device_reason,
            "count": len(assets_out),

            "inserted_count": len(inserted_serials),
            "skipped_count": len(skipped_serials),
            "already_printed_count": len(already_printed_serials),

            "inserted_serials": inserted_serials,
            "skipped_serials": skipped_serials,
            "already_printed_serials": already_printed_serials,

            "printed": printed,
            "print_message": print_message,

            "db_updated_to_printed": updated_to_printed,
            "printed_serials": will_print_serials if printed else [],

            "assets": assets_out,
            "tspl_preview": tspl_list if return_tspl else None
        }), 200

    except psycopg2.OperationalError as e:
        return jsonify({
            "status": "error",
            "message": "PostgreSQL connection failed",
            "error": str(e),
            "fix_tips": [
                "Use IPv4 host: PG_HOST=127.0.0.1",
                "Check PG_USER/PG_PASS",
                "If you see 'no encryption' try PG_SSLMODE=require"
            ]
        }), 500

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Failed to generate/print labels",
            "error": str(e)
        }), 500


@app.route("/api/assets/online-devices", methods=["GET"])
def api_assets_online_devices():
    try:
        limit = int(request.args.get("limit", "500"))
        limit = max(1, min(limit, 5000))

        dsn = _build_dsn()

        with psycopg2.connect(dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        a.id,
                        a.serial,
                        a.qr_status,
                        a.created_at,
                        si.status AS system_status,
                        si.hostname,
                        si.ip_address
                    FROM public.assets a
                    JOIN LATERAL (
                        SELECT status, hostname, ip_address
                        FROM public.system_information
                        WHERE serial_number = a.serial
                        ORDER BY created_at DESC NULLS LAST
                        LIMIT 1
                    ) si ON TRUE
                    WHERE UPPER(COALESCE(si.status, '')) IN ('ACTIVE', 'ONLINE')
                    ORDER BY a.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        return jsonify({
            "status": "success",
            "count": len(rows),
            "devices": rows
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Failed to fetch online devices",
            "error": str(e)
        }), 500


@app.route("/api/assets/latest", methods=["GET"])
def api_assets_latest():
    try:
        limit = int(request.args.get("limit", "50"))
        limit = max(1, min(limit, 500))

        with psycopg2.connect(_build_dsn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, serial, qr_status, created_at
                    FROM public.assets
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        return jsonify({"status": "success", "count": len(rows), "assets": rows}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": "Failed to fetch latest assets", "error": str(e)}), 500


pratap_CONFIG = {
    "host": "20.40.46.161",
    "port": 5432,
    "dbname": "face_recog_new",
    "user": "postgres",
    "password": "qwer1234"
}

def get_dealer_code(user_id):
    """
    Return dealer_code if the given user is a dealer; otherwise None.
    """
    if not user_id:
        return None
    
    try:
        conn = psycopg2.connect(**pratap_CONFIG)
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
        cursor.close()
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
    
    try:
        conn = psycopg2.connect(**pratap_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
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
        cursor.close()
        conn.close()


@app.route('/dealer/customers', methods=['GET'])
def get_dealer_customers():
    """
    Fetch customers linked to a dealer.
    Query params: dealer_id (required), limit (optional, default 50), page (optional, default 1)
    """
    try:
        # ✅ SECURITY FIX: Validate and convert dealer_id
        dealer_id = request.args.get('dealer_id')
        if not dealer_id:
            return jsonify({"message": "Dealer ID is required"}), 400
        
        try:
            dealer_id = int(dealer_id)
        except (TypeError, ValueError):
            return jsonify({"message": "Invalid dealer ID format"}), 400
        
        # ✅ SECURITY FIX: Verify user is actually a dealer
        dealer_code = get_dealer_code(dealer_id)
        if not dealer_code:
            return jsonify({"message": "Access denied: user is not a dealer"}), 403
        
        # ✅ IMPROVEMENT: Validate pagination parameters safely
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
        
        # ✅ IMPROVEMENT: Fetch data with error handling
        rows = get_customers_by_dealer_code(dealer_code, limit=limit, offset=offset)
        
        # ✅ IMPROVEMENT: Build response with safer data extraction
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
    

# @app.route("/api/scan-db", methods=["POST"])
# def api_scan_db():
#     """
#     UI -> POST /api/scan-db
#     Forwards batch to remote:
#     https://192.168.1.111:5001/api/db/register-device-analytics
#     """

#     # ✅ primary + optional fallback
#     remote_urls = [
#         "https://192.168.1.44:5001/api/db/register-device-analytics",
#     ]

#     try:
#         payload = request.get_json(silent=True) or {}
#         devices = payload.get("devices", [])

#         if not isinstance(devices, list) or len(devices) == 0:
#             return jsonify({"ok": False, "message": "No devices provided"}), 400

#         # normalize/validate
#         clean_devices = []
#         for idx, it in enumerate(devices):
#             ip_address = (it.get("ip_address") or it.get("ip") or "").strip()
#             device_rtsp = (it.get("device_rtsp") or it.get("rtsp") or it.get("rtsp_url") or "").strip() or None

#             analytics = it.get("analytics") or []
#             if not isinstance(analytics, list):
#                 analytics = []

#             clean_analytics = []
#             for a in analytics:
#                 analytics_name = (a.get("analytics_name") or a.get("name") or "").strip()
#                 artsp = (a.get("rtsp_url") or a.get("rtsp") or "").strip() or None
#                 if not analytics_name:
#                     continue
#                 clean_analytics.append({"analytics_name": analytics_name, "rtsp_url": artsp})

#             if not ip_address:
#                 return jsonify({"ok": False, "message": f"ip_address required at index {idx}"}), 400

#             if len(clean_analytics) == 0:
#                 continue

#             clean_devices.append({
#                 "ip_address": ip_address,
#                 "device_rtsp": device_rtsp,
#                 "analytics": clean_analytics
#             })

#         if len(clean_devices) == 0:
#             return jsonify({"ok": False, "message": "No valid devices with analytics found"}), 400

#         forward_payload = {"devices": clean_devices}

#         print("=================================================")
#         print(f"📦 Devices count: {len(clean_devices)}")
#         print("📡 Forwarding payload to remote URLs (in order):")
#         for u in remote_urls:
#             print("   ->", u)
#         print("=================================================")

#         # ✅ IMPORTANT: Use Session + disable env proxy
#         session = requests.Session()
#         session.trust_env = False  # ✅ ignores HTTP_PROXY / HTTPS_PROXY from environment

#         last_attempt_details = []
#         for remote_url in remote_urls:
#             try:
#                 print(f"\n➡️ TRY remote_url = {remote_url}")

#                 resp = session.post(
#                     remote_url,
#                     json=forward_payload,
#                     verify=False,
#                     timeout=(10, 60),          # connect timeout, read timeout
#                     allow_redirects=False,     # ✅ so we can see redirect target
#                     proxies={"http": None, "https": None}  # ✅ force no proxy
#                 )

#                 # ✅ capture redirect info (if any)
#                 location = resp.headers.get("Location")
#                 server_hdr = resp.headers.get("Server")
#                 via_hdr = resp.headers.get("Via")
#                 xff = resp.headers.get("X-Forwarded-For")

#                 print("✅ status_code =", resp.status_code)
#                 print("✅ resp.url    =", resp.url)
#                 print("✅ Location    =", location)
#                 print("✅ Server      =", server_hdr)
#                 print("✅ Via         =", via_hdr)
#                 print("✅ XFF         =", xff)
#                 print("✅ body(head)  =", (resp.text or "")[:300])

#                 # parse remote response safely
#                 try:
#                     remote_json = resp.json()
#                 except Exception as e:
#                     remote_json = {"raw": (resp.text or "")[:2000], "parse_error": str(e)}

#                 attempt = {
#                     "remote_url": remote_url,
#                     "status_code": resp.status_code,
#                     "final_url": getattr(resp, "url", None),
#                     "location": location,
#                     "server": server_hdr,
#                     "via": via_hdr,
#                     "x_forwarded_for": xff,
#                     "body": remote_json,
#                 }
#                 last_attempt_details.append(attempt)

#                 # ✅ If redirect happened, stop and show it clearly
#                 if resp.status_code in (301, 302, 307, 308) and location:
#                     return jsonify({
#                         "ok": False,
#                         "message": "Remote is redirecting. Your request may be landing on another server.",
#                         "redirect_to": location,
#                         "attempts": last_attempt_details
#                     }), 200

#                 # ✅ FIXED: Check response status correctly
#                 # The remote returns: {"status": "ok", "count": X, "results": [...]}
#                 remote_status = ""
#                 if isinstance(remote_json, dict):
#                     remote_status = (remote_json.get("status") or "").lower()

#                 # Success conditions:
#                 # 1. HTTP status is 200 or 201
#                 # 2. Remote response has "status": "ok"
#                 ok = (resp.status_code in (200, 201)) and (remote_status == "ok")

#                 print(f"✅ Remote status: {remote_status}, HTTP status: {resp.status_code}, OK: {ok}")

#                 if ok:
#                     # ✅ Forward the remote response back to client
#                     return jsonify({
#                         "ok": True,
#                         "message": remote_json.get("message", "Devices saved successfully"),
#                         "count": remote_json.get("count"),
#                         "remote_response": remote_json,
#                         "attempts": last_attempt_details
#                     }), 200

#                 # If not ok, try next remote URL (fallback)
#                 print(f"⚠ Remote returned not-ok status: {remote_status}. Trying next URL (if any)...")

#             except requests.exceptions.Timeout:
#                 last_attempt_details.append({
#                     "remote_url": remote_url,
#                     "error": "timeout",
#                 })
#                 print("⏳ Timeout on", remote_url)

#             except requests.exceptions.ConnectionError as e:
#                 last_attempt_details.append({
#                     "remote_url": remote_url,
#                     "error": "connection_error",
#                     "details": str(e),
#                 })
#                 print("🔌 ConnectionError on", remote_url, str(e))

#             except Exception as e:
#                 last_attempt_details.append({
#                     "remote_url": remote_url,
#                     "error": "exception",
#                     "details": str(e),
#                 })
#                 print("❌ Exception on", remote_url, str(e))

#         # none succeeded
#         return jsonify({
#             "ok": False,
#             "message": "All remote endpoints failed or returned not-ok status",
#             "attempts": last_attempt_details
#         }), 200

#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({
#             "ok": False,
#             "message": "Internal server error",
#             "error": str(e),
#             "traceback": traceback.format_exc()
#         }), 200
@app.route("/api/scan-db", methods=["POST"])
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
                    cur.execute(
                        """
                        SELECT serial_number
                        FROM public.device_master
                        WHERE serial_number IS NOT NULL
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """
                    )
                    result = cur.fetchone()
                    
                    if result:
                        detected_serial = result['serial_number']
                        
                        cur.execute(
                            """
                            SELECT ip_address
                            FROM public.system_information
                            WHERE serial_number = %s
                            AND ip_address IS NOT NULL
                            ORDER BY created_at DESC
                            LIMIT 1
                            """,
                            (detected_serial,)
                        )
                        serial_row = cur.fetchone()
                        
                        if serial_row:
                            ip_data = serial_row['ip_address']
                            
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
                            
                            print(f"   📊 TARGET_IP: {TARGET_IP}, agent_id: {agent_id}")
        except Exception as e:
            print(f"❌ Database error: {e}")
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

        # ✅ IMPORTANT: Send COMPLETE scan data as received
        # Don't normalize or filter - send everything to client
        user_id = payload.get("user_id")
        scan_data = {
            "devices": devices,  # Complete original data
            "user_id": user_id,
            "timestamp": time.time(),
            "source": "distributor_dashboard"
        }

        print("=" * 80)
        print(f"📦 Total devices: {len(devices)}")
        print(f"🎯 Target agent_id: {agent_id}")
        print(f"📡 Sending complete scan data via socket...")
        print(f"📄 Sample device: {json.dumps(devices[0] if devices else {}, indent=2)}")
        print("=" * 80)

        # ✅ Send complete scan data via socket
        try:
            print(f"\n🔌 Sending 'receive_scan_data' command to agent {agent_id}...")
            
            socket_response = socket_hub_send_command(
                agent_id=agent_id,
                command="receive_scan_data",  # New command name
                data=scan_data,
                timeout=90  # Increased timeout for large data
            )
            
            print(f"✅ Socket response received:")
            print(json.dumps(socket_response, indent=2))
            
            # Check response
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
                print(f"❌ Socket command failed: {error_msg}")
                
                return jsonify({
                    "ok": False,
                    "message": f"Failed to save scan data: {error_msg}",
                    "method": "socket",
                    "agent_id": agent_id,
                    "socket_response": socket_response
                }), 500
                
        except Exception as e:
            print(f"❌ Socket error: {str(e)}")
            traceback.print_exc()
            
            return jsonify({
                "ok": False,
                "message": "Socket communication failed",
                "error": str(e),
                "agent_id": agent_id
            }), 500

    except Exception as e:
        print(f"❌ Fatal error in api_scan_db:")
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "message": "Internal server error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
        
@app.get("/dealer/devices")
def api_dealer_devices():
    """
    Dealer ko issue hue devices return karta hai device_master table se.

    Query params:
      dealer_id  = dealer ka user_signups.id  (required)
      filter     = online | all               (default: all)

    Kya return hota hai:
    - Sirf wahi devices jo dealer_id se match karti hain device_master mein
    - Latest status device_status se join hoti hai
    - System info bhi attach hoti hai
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
        with psycopg2.connect(**DB_CONFIG) as conn:
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


@app.get("/distributor/devices")
def api_distributor_devices():
    """
    Distributor ko issue hue devices return karta hai device_master table se.

    Query params:
      distributor_id  = distributor ka user_signups.id  (required)
      filter          = online | all                     (default: all)

    Kya return hota hai:
    - Sirf wahi devices jo distributor_id se match karti hain device_master mein
    - Dealer ke through issue hue devices bhi include honge agar distributor match karta ho
    - Latest status device_status se join hoti hai
    - System info bhi attach hoti hai
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
        with psycopg2.connect(**DB_CONFIG) as conn:
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


@app.route("/")
def index():
    return render_template("sign_up.html", server_ip=request.host.split(":")[0])

import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRENTER_PY = os.path.join(BASE_DIR, "prenter_v3.py") 
PRENTER_FILENAME = "prenter_v3.py"   
PRENTER_PY = os.path.join(BASE_DIR, PRENTER_FILENAME)

def run_printer(serial: str):
    # cmd = [
    #     sys.executable,
    #     PRENTER_PY,
    #     "--printer", "pgak",
    #     "--count", "1",
    #     "--qr-template", serial,
    #     "--label-w-mm", "100",
    #     "--label-h-mm", "70",
    #     "--qr-cell", "12",
    #     "--qr-x", "450",
    #     "--qr-y", "260",
    # ]

    cmd = [
        sys.executable,
        "prenter_v3.py",

        "--printer", "pgak",

        "--mode", "serial",
        "--serial", serial,          # e.g. "10000000bd228090"

        "--count", "1",

        "--label-w-mm", "60",
        "--label-h-mm", "30",
        "--gap-mm", "2",

        "--dpi", "300",

        "--qr-cell", "9",

        "--speed", "2",
        "--density", "12",

        "--only-qr",
        "--center-qr",
    ]

    result = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120
    )
    return cmd, result

@app.route("/api/print-serial", methods=["POST"])
def api_print_serial():
    try:
        data = request.get_json(force=True) or {}
        hostname = (data.get("hostname") or "").strip() or "N/A"
        serial = (data.get("serial_number") or "").strip() or "N/A"

        if not os.path.exists(PRENTER_PY):
            return jsonify({"ok": False, "error": f"Printer script not found: {PRENTER_PY}"}), 500

        print(f"[PRINT] hostname='{hostname}' serial='{serial}'")

        cmd, result = run_printer(serial)

        print("[PRINT] CMD:", " ".join(cmd))
        print("[PRINT] returncode:", result.returncode)
        if result.stdout:
            print("[PRINT] stdout:\n", result.stdout)
        if result.stderr:
            print("[PRINT] stderr:\n", result.stderr)

        if result.returncode != 0:
            return jsonify({
                "ok": False,
                "error": f"Printer script failed with return code {result.returncode}",
                "cmd": cmd,
                "stdout": result.stdout,
                "stderr": result.stderr
            }), 500

        return jsonify({"ok": True, "printer": "pgak", "stdout": result.stdout})

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Timeout: printing script took too long"}), 500
    except Exception as e:
        tb = traceback.format_exc()
        print("[PRINT] EXCEPTION:\n", tb)
        return jsonify({"ok": False, "error": str(e), "traceback": tb}), 500


app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

BASE_IMAGE_API = os.getenv("BASE_IMAGE_API", "http://127.0.0.1:8000/images/").strip()
if not BASE_IMAGE_API.endswith("/"):
    BASE_IMAGE_API += "/"

IMAGE_ROOT = os.getenv("IMAGE_ROOT", str(Path("static") / "images"))
IMAGE_ROOT_PATH = Path(IMAGE_ROOT)
IMAGE_ROOT_PATH.mkdir(parents=True, exist_ok=True)

ALLOWED_TAGS = {"jpg", "png", "webp", "bmp"}

IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


# ---------------------------
# Static serving (FastAPI mount replacement)
# ---------------------------
@app.route("/images/<path:filename>", methods=["GET"])
def images(filename):
    return send_from_directory(str(IMAGE_ROOT_PATH), filename)


# ---------------------------
# Helpers
# ---------------------------

def _extract_ip_from_anywhere() -> str:
    """
    Accept ip from:
      - multipart form: ip / ip_address / device_ip
      - query params:   ip / ip_address / device_ip
      - json body:      ip / ip_address / device_ip
    """
    # multipart/form-data
    ip = (request.form.get("ip")
          or request.form.get("ip_address")
          or request.form.get("device_ip"))

    # query param
    if not ip:
        ip = (request.args.get("ip")
              or request.args.get("ip_address")
              or request.args.get("device_ip"))

    # json body
    if not ip:
        js = request.get_json(silent=True) or {}
        if isinstance(js, dict):
            ip = js.get("ip") or js.get("ip_address") or js.get("device_ip")

    return (ip or "").strip()


def validate_ipv4(ip: str) -> str:
    ip = (ip or "").strip()
    if not ip:
        raise ValueError("Missing ip field")

    # If someone sent rtsp url, extract hostname
    if "://" in ip:
        try:
            parsed = urlparse(ip)
            ip = (parsed.hostname or "").strip()
        except Exception:
            pass

    # If someone sent ip:port, remove port
    if ":" in ip and IPV4_RE.match(ip.split(":")[0].strip()):
        ip = ip.split(":")[0].strip()

    ip = ip.strip()
    if not IPV4_RE.match(ip):
        raise ValueError(f"Invalid IP format: '{ip}'")

    parts = ip.split(".")
    for p in parts:
        n = int(p)
        if n < 0 or n > 255:
            raise ValueError("Invalid IP value")
    return ip


def normalize_tag_from_file(filename: str, mimetype: str | None) -> str:
    ext = Path(filename).suffix.lower().strip(".")
    if ext == "jpeg":
        ext = "jpg"

    if ext in ALLOWED_TAGS:
        return ext

    if mimetype:
        mt = mimetype.lower()
        if "jpeg" in mt:
            return "jpg"
        if "png" in mt:
            return "png"
        if "webp" in mt:
            return "webp"
        if "bmp" in mt:
            return "bmp"

    raise ValueError("Unsupported image type (allowed: jpg/jpeg, png, webp, bmp)")


def filename_for_ip(ip: str, tag: str) -> str:
    return f"{ip}_{tag}"


def mimetype_for_tag(tag: str) -> str:
    return {
        "jpg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(tag, "application/octet-stream")


def atomic_write_bytes(final_path: Path, data: bytes) -> None:
    tmp_name = f".tmp_{uuid.uuid4().hex}"
    tmp_path = final_path.parent / tmp_name
    tmp_path.write_bytes(data)
    os.replace(str(tmp_path), str(final_path))


def build_public_url(saved_filename: str) -> str:
    return BASE_IMAGE_API + saved_filename


def find_existing_snapshot(ip: str, preferred_tag: str | None = None) -> tuple[Path | None, str | None]:
    tags_to_try = [preferred_tag] if preferred_tag else ["jpg", "png", "webp", "bmp"]

    for tag in tags_to_try:
        name = filename_for_ip(ip, tag)
        p = IMAGE_ROOT_PATH / name
        if p.exists() and p.is_file():
            return p, tag

    return None, None


# ---------------------------
# Routes
# ---------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "image_root_disk": str(IMAGE_ROOT_PATH),
        "base_image_api": BASE_IMAGE_API
    })


@app.route("/api/images/upload", methods=["POST"])
def upload_image():
    """
    Accepts ip from form/query/json.
    """
    try:
        raw_ip = _extract_ip_from_anywhere()
        ip = validate_ipv4(raw_ip)

        f = request.files.get("image") or request.files.get("file")
        if not f:
            return jsonify({"ok": False, "message": "Missing file field: image (or file)"}), 400

        if not f or not f.filename:
            return jsonify({"ok": False, "message": "Empty filename"}), 400

        original_name = secure_filename(f.filename)
        tag = normalize_tag_from_file(original_name, f.mimetype)

        save_name = filename_for_ip(ip, tag)
        save_path = IMAGE_ROOT_PATH / save_name

        data = f.read()
        if not data:
            return jsonify({"ok": False, "message": "Empty file content"}), 400

        atomic_write_bytes(save_path, data)

        return jsonify({
            "ok": True,
            "ip": ip,
            "saved_as": save_name,
            "format": tag,
            "stored_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),

            # ✅ save this in DB
            "image_url": build_public_url(save_name),

            "get_url_static": f"/images/{save_name}",
            "get_url_api": f"/api/images/{ip}",
            "get_url_api_exact": f"/api/images/{ip}?format={tag}",
        })

    except ValueError as ve:
        return jsonify({"ok": False, "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": "Upload failed", "error": str(e)}), 500


@app.route("/api/images/<ip>", methods=["GET"])
def get_image(ip: str):
    try:
        ip = validate_ipv4(ip)

        preferred = (request.args.get("format") or "").strip().lower()
        if preferred == "jpeg":
            preferred = "jpg"
        if preferred and preferred not in ALLOWED_TAGS:
            return jsonify({"ok": False, "message": f"Invalid format. Allowed: {sorted(ALLOWED_TAGS)}"}), 400

        p, tag = find_existing_snapshot(ip, preferred_tag=preferred if preferred else None)
        if not p:
            return jsonify({"ok": False, "message": "Snapshot not found for this IP"}), 404

        resp = make_response(send_file(str(p), mimetype=mimetype_for_tag(tag), as_attachment=False))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    except ValueError as ve:
        return jsonify({"ok": False, "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": "Get failed", "error": str(e)}), 500


@app.route("/api/images/status/<ip>", methods=["GET"])
def image_status(ip: str):
    try:
        ip = validate_ipv4(ip)
        found = []

        for tag in ["jpg", "png", "webp", "bmp"]:
            name = filename_for_ip(ip, tag)
            p = IMAGE_ROOT_PATH / name
            if p.exists() and p.is_file():
                found.append({
                    "format": tag,
                    "saved_as": name,
                    "image_url": build_public_url(name),
                    "get_url_static": f"/images/{name}",
                    "get_url_api": f"/api/images/{ip}?format={tag}",
                })

        return jsonify({"ok": True, "ip": ip, "found": found})

    except ValueError as ve:
        return jsonify({"ok": False, "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": "Status failed", "error": str(e)}), 500
    

# =============================================================================
# PROXY ROUTES: /api/db/analytics  &  /api/db/devices
# 
# Dashboard → these server routes → socket_hub_send_command → agent (clienttt.py)
# Agent handles DB read/write directly via its own routes.
# Same pattern as /api/scan-db above.
# =============================================================================

def _get_agent_ip_for_proxy():
    """
    Get active agent IP from device_master + system_information tables.
    Returns (TARGET_IP, agent_id) tuple or raises Exception.
    """
    TARGET_IP = None
    agent_id  = None

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
                        agent_id  = val
                        break
            elif isinstance(ip_data, str) and not ip_data.startswith('127.'):
                TARGET_IP = ip_data
                agent_id  = ip_data

    if not TARGET_IP:
        print(f"❌ [proxy] ip_data={ip_data!r} — no valid non-loopback IP found")
        raise Exception("No valid (non-loopback) agent IP found")

    print(f"🎯 Proxy → agent IP: {TARGET_IP}")
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
    print(f"🔌 Socket response for [{command}]: {resp}")
    return resp


# ─── ANALYTICS: GET all ────────────────────────────────────────────
@app.route("/api/db/analytics", methods=["GET"])
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
                "count":     inner.get("count", 0),
                "analytics": inner.get("analytics", [])
            }), 200
        else:
            # 503 = agent not connected / timed out (operational, not a server crash)
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"⚠️  [analytics] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── ANALYTICS: GET by id ──────────────────────────────────────────
@app.route("/api/db/analytics/<int:analytic_id>", methods=["GET"])
def proxy_get_analytic_by_id(analytic_id):
    """Proxy: GET agent /api/db/analytics/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "get_analytic_by_id", {"analytic_id": analytic_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "analytic": (resp.get("data") or {}).get("analytic", {})}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"⚠️  [analytics/{analytic_id}] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── ANALYTICS: PUT (update) ───────────────────────────────────────
@app.route("/api/db/analytics/<int:analytic_id>", methods=["PUT"])
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
            print(f"⚠️  [analytics/{analytic_id} PUT] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── ANALYTICS: DELETE ─────────────────────────────────────────────
@app.route("/api/db/analytics/<int:analytic_id>", methods=["DELETE"])
def proxy_delete_analytic(analytic_id):
    """Proxy: DELETE agent /api/db/analytics/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "delete_analytic", {"analytic_id": analytic_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Analytic {analytic_id} deleted"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"⚠️  [analytics/{analytic_id} DELETE] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── DEVICES: GET all ──────────────────────────────────────────────
@app.route("/api/db/devices", methods=["GET"])
def proxy_get_devices():
    """Proxy: GET agent /api/db/devices (optional ?ip_address= or ?user_id=)"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "get_devices", {
            "ip_address": request.args.get("ip_address", ""),
            "user_id":    request.args.get("user_id", "")
        })

        if resp.get("ok") or resp.get("status") == "ok":
            inner = resp.get("data") or {}
            return jsonify({
                "status":  "ok",
                "count":   inner.get("count", 0),
                "devices": inner.get("devices", [])
            }), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"⚠️  [devices] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── DEVICES: PUT (update) ─────────────────────────────────────────
@app.route("/api/db/devices/<int:device_id>", methods=["PUT"])
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
            print(f"⚠️  [devices/{device_id} PUT] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── DEVICES: DELETE ───────────────────────────────────────────────
@app.route("/api/db/devices/<int:device_id>", methods=["DELETE"])
def proxy_delete_device(device_id):
    """Proxy: DELETE agent /api/db/devices/<id>"""
    try:
        _, agent_id = _get_agent_ip_for_proxy()
        resp = _proxy_via_socket(agent_id, "delete_device", {"device_id": device_id})

        if resp.get("ok") or resp.get("status") == "ok":
            return jsonify({"status": "ok", "message": f"Device {device_id} deleted"}), 200
        else:
            err_msg = resp.get("error") or resp.get("message", "Agent error")
            print(f"⚠️  [devices/{device_id} DELETE] Agent returned error: {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    print(f"[DEBUG] Registered routes: {len(list(app.url_map.iter_rules()))}")
    for rule in app.url_map.iter_rules():
        print(f"  {rule.rule} -> {rule.endpoint} [{','.join(rule.methods)}]")
    threading.Thread(target=inactive_watcher, daemon=True).start()
    start_socket_hub()
    app.run(
        host="0.0.0.0",
        port=SERVER_PORT,
        debug=True,
        use_reloader=False,
        # ssl_context=(CERT_FILE, KEY_FILE)
    )