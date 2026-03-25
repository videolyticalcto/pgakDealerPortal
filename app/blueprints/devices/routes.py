"""
Devices Blueprint routes.

Handles: /socket-hub/agents, /device_discovery, /devices, /devices_status,
         /system_info, /snapshots, /images, /health,
         /api/images/upload, /api/images/<ip>, /api/images/status/<ip>,
         /api/print-serial
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from flask import request, jsonify, send_from_directory, send_file, make_response, abort
from werkzeug.utils import secure_filename

from app.blueprints.devices import devices_bp
from app.config import Config
from app.extensions import DEVICES, DISCOVERED, SNAPSHOT_DIR_MAP
from app.socket_hub import _SOCKET_AGENT_CONNS, _SOCKET_AGENT_LOCK

logger = logging.getLogger(__name__)

# ── Config values ──────────────────────────────────────────────────────────
DEFAULT_AGENT_SCHEME = Config.DEFAULT_AGENT_SCHEME
DEFAULT_AGENT_PORT = Config.DEFAULT_AGENT_PORT
HEARTBEAT_TIMEOUT = Config.HEARTBEAT_TIMEOUT

SCAN_ALL_AGENTS = os.getenv("SCAN_ALL_AGENTS", "true").strip().lower() in ("1", "true", "yes")
AGENT_VERIFY_SSL = os.getenv("AGENT_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")
AGENT_CA_BUNDLE = os.getenv("AGENT_CA_BUNDLE", "").strip()

PG_SSLMODE = os.getenv("PG_SSLMODE", "prefer")

# ── In-memory stores for agents (device_discovery verified) ────────────────
AGENTS_LOCK = threading.Lock()
AGENTS = {}

# ── Image config ───────────────────────────────────────────────────────────
BASE_IMAGE_API = os.getenv("BASE_IMAGE_API", "http://127.0.0.1:8000/images/").strip()
if not BASE_IMAGE_API.endswith("/"):
    BASE_IMAGE_API += "/"

IMAGE_ROOT = os.getenv("IMAGE_ROOT", str(Path("static") / "images"))
IMAGE_ROOT_PATH = Path(IMAGE_ROOT)
IMAGE_ROOT_PATH.mkdir(parents=True, exist_ok=True)

ALLOWED_TAGS = {"jpg", "png", "webp", "bmp"}
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# ── Printer config ─────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PRENTER_FILENAME = "prenter_v3.py"
PRENTER_PY = os.path.join(BASE_DIR, PRENTER_FILENAME)

# ── Snapshot dir ───────────────────────────────────────────────────────────
SNAPSHOT_DIR = r"\\192.168.1.111\pi4\SharedFolder"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _build_dsn() -> str:
    return (
        f"host={Config.DB_CONFIG['host']} port={Config.DB_CONFIG['port']} dbname={Config.DB_CONFIG['dbname']} "
        f"user={Config.DB_CONFIG['user']} password={Config.DB_CONFIG['password']} sslmode={PG_SSLMODE}"
    )


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_remote_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


def normalize_agent_url(agent_scheme: str, agent_ip: str, agent_port: int, path: str) -> str:
    base = f"{agent_scheme}://{agent_ip}:{agent_port}/"
    return urljoin(base, path.lstrip("/"))


def agent_verify_value():
    if AGENT_VERIFY_SSL:
        return AGENT_CA_BUNDLE if AGENT_CA_BUNDLE else True
    return False

AGENT_VERIFY = agent_verify_value()


def get_snapshot_dir(target_ip):
    """Get the appropriate SNAPSHOT_DIR based on target IP."""
    if not target_ip:
        return None
    snapshot_dir = SNAPSHOT_DIR_MAP.get(target_ip)
    if snapshot_dir:
        print(f"   SNAPSHOT_DIR selected for {target_ip}: {snapshot_dir}")
    else:
        print(f"   WARNING: No SNAPSHOT_DIR configured for {target_ip}")
    return snapshot_dir


def _extract_first_ip(ip_dict_or_value) -> Optional[str]:
    """Extract a usable (non-loopback) IP from the IP Address payload."""

    def _clean(v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
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

        for pref in preferred_prefixes:
            for iface, ip in items:
                if iface.lower().startswith(pref) and _is_good_ip(ip):
                    return ip

        for iface, ip in items:
            if iface.lower() in ("lo", "loopback"):
                continue
            if _is_good_ip(ip):
                return ip

        for iface, ip in items:
            if iface.lower() in ("lo", "loopback"):
                continue
            if ip.startswith("127.") or ip == "::1":
                continue
            return ip

        return None

    ip = _clean(ip_dict_or_value)
    if not ip:
        return None
    if ip.startswith("127.") or ip == "::1":
        return None
    return ip


def write_status_to_db(info: dict, status: str):
    hostname = info.get("Hostname")
    machine_val = (info.get("Machine (OS Type)") or info.get("Machine") or info.get("Machine Type"))

    with psycopg2.connect(**Config.DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # Upsert: INSERT on conflict UPDATE
            cur.execute("""
                INSERT INTO system_information (
                    hostname, os, os_version, kernel_version,
                    make, model, serial_number, processor,
                    machine_type, mac_addresses, ip_address,
                    status, created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (hostname) DO UPDATE SET
                    status = EXCLUDED.status,
                    os = COALESCE(NULLIF(system_information.os,''), EXCLUDED.os),
                    os_version = COALESCE(NULLIF(system_information.os_version,''), EXCLUDED.os_version),
                    kernel_version = COALESCE(NULLIF(system_information.kernel_version,''), EXCLUDED.kernel_version),
                    make = COALESCE(NULLIF(system_information.make,''), EXCLUDED.make),
                    model = COALESCE(NULLIF(system_information.model,''), EXCLUDED.model),
                    serial_number = COALESCE(NULLIF(system_information.serial_number,''), EXCLUDED.serial_number),
                    processor = COALESCE(NULLIF(system_information.processor,''), EXCLUDED.processor),
                    machine_type = COALESCE(NULLIF(system_information.machine_type,''), EXCLUDED.machine_type),
                    mac_addresses = COALESCE(system_information.mac_addresses, EXCLUDED.mac_addresses),
                    ip_address = EXCLUDED.ip_address
            """, (
                hostname,
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
                status
            ))
        conn.commit()


def update_device_status(info: dict, sys_status: str):
    new_status = "ONLINE" if sys_status == "ACTIVE" else "OFFLINE"

    ip_map = info.get("IP Address") or {}
    ip_address = _extract_first_ip(ip_map)
    hostname = info.get("Hostname")

    if not ip_address:
        return

    now = datetime.now()

    with psycopg2.connect(**Config.DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, online_at, offline_at
                FROM device_status
                WHERE hostname=%s
                ORDER BY last_change_at DESC
                LIMIT 1
            """, (hostname,))
            row = cur.fetchone()

            if row is None:
                ts_col = "online_at" if new_status == "ONLINE" else "offline_at"
                cur.execute(f"""
                    INSERT INTO device_status
                    (ip_address, hostname, status, {ts_col}, last_change_at)
                    VALUES (%s,%s,%s,%s,NOW())
                """, (ip_address, hostname, new_status, now))
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
                    cur.execute("""
                        UPDATE device_status
                        SET ip_address=%s, last_change_at=NOW()
                        WHERE hostname=%s
                    """, (ip_address, hostname))
        conn.commit()


def run_printer(serial: str):
    cmd = [
        sys.executable,
        "prenter_v3.py",
        "--printer", "pgak",
        "--mode", "serial",
        "--serial", serial,
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


# ── Image helpers ──────────────────────────────────────────────────────────

def _extract_ip_from_anywhere() -> str:
    """Accept ip from multipart form, query params, or json body."""
    ip = (request.form.get("ip")
          or request.form.get("ip_address")
          or request.form.get("device_ip"))

    if not ip:
        ip = (request.args.get("ip")
              or request.args.get("ip_address")
              or request.args.get("device_ip"))

    if not ip:
        js = request.get_json(silent=True) or {}
        if isinstance(js, dict):
            ip = js.get("ip") or js.get("ip_address") or js.get("device_ip")

    return (ip or "").strip()


def validate_ipv4(ip: str) -> str:
    ip = (ip or "").strip()
    if not ip:
        raise ValueError("Missing ip field")

    if "://" in ip:
        try:
            parsed = urlparse(ip)
            ip = (parsed.hostname or "").strip()
        except Exception:
            pass

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


# =============================================================================
# BACKGROUND WATCHER (check inactive devices)
# =============================================================================

def inactive_watcher():
    while True:
        now = time.time()
        for host, d in list(DEVICES.items()):
            if now - d["last_seen_ts"] > HEARTBEAT_TIMEOUT:
                if d["status"] != "INACTIVE":
                    d["status"] = "INACTIVE"
                    d["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    write_status_to_db(d["info"], "INACTIVE")
                    update_device_status(d["info"], "INACTIVE")
        time.sleep(2)


# =============================================================================
# ROUTES
# =============================================================================

@devices_bp.route("/socket-hub/agents", methods=["GET"])
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


@devices_bp.route("/system_info", methods=["POST"])
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


@devices_bp.route("/device_discovery", methods=["POST"])
def device_discovery():
    """
    Device discovery will run ONLY on the last updated device from device_master.
    Uses direct JOIN query for latest updated device.
    """
    payload = request.get_json(silent=True)
    print(payload, ")")

    if payload is None:
        raw = request.get_data(as_text=True)
        return jsonify({
            "ok": False,
            "message": "Invalid/missing JSON. Send Content-Type: application/json",
            "content_type": request.content_type,
            "raw_preview": (raw[:500] if raw else "")
        }), 400

    agent_ip = payload.get("IP") or payload.get("ip") or get_remote_ip()
    agent_scheme = (payload.get("agent_scheme") or DEFAULT_AGENT_SCHEME).strip().lower()
    agent_port = int(payload.get("agent_port") or DEFAULT_AGENT_PORT)

    TARGET_IP = None
    TARGET_SERIAL = None
    UPDATED_AT = None

    try:
        with psycopg2.connect(_build_dsn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
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

                    if isinstance(ip_data, dict):
                        # Priority 1: eth interfaces (wired)
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
                    print(f"LATEST UPDATED DEVICE:")
                    print(f"   Serial:    {TARGET_SERIAL}")
                    print(f"   Updated:   {UPDATED_AT}")
                    print(f"   IP data:   {ip_data}")
                    print(f"   Target IP: {TARGET_IP}")
                    print(f"{'='*80}")

    except Exception as e:
        print(f"Database error: {e}")
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
    print(f"HEARTBEAT RECEIVED")
    print(f"   Sender: {agent_ip}")
    print(f"   Target: {TARGET_IP} (last updated)")
    print(f"{'='*80}")

    if agent_ip != TARGET_IP:
        print(f"   REJECTED: {agent_ip} != {TARGET_IP}")
        print(f"{'='*80}\n")
        return jsonify({
            "ok": False,
            "message": f"Only accepting from {TARGET_IP}",
            "target_ip": TARGET_IP,
            "target_serial": TARGET_SERIAL,
            "received_ip": agent_ip
        }), 403

    print(f"   ACCEPTED - Device discovery ENABLED")

    key = agent_ip
    print(key, "---------------------------")
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

    print(f"   Registered: {key}")
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


@devices_bp.route("/devices_status", methods=["GET"])
def api_device_status():
    """Return latest device status per device from `device_status`."""
    filter_type = (request.args.get("filter", "online") or "online").strip().lower()
    if filter_type not in ("all", "online"):
        filter_type = "online"

    def _dt_str(v):
        try:
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        return v

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
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (filter_type,))
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
                'id': r.get('id'),
                'ip_address': r.get('ip_address'),
                'hostname': r.get('hostname'),
                'status': r.get('status'),
                'last_seen': _dt_str(r.get('last_seen')),
                'last_change_at': _dt_str(r.get('last_change_at')),
                'info': info
            }

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


@devices_bp.route("/devices", methods=["GET"])
def devices_list():
    return jsonify(DEVICES)


@devices_bp.route("/snapshots/<path:filename>")
def serve_snapshot(filename):
    full_path = os.path.join(SNAPSHOT_DIR, filename)

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


@devices_bp.route("/images/<path:filename>", methods=["GET"])
def images(filename):
    return send_from_directory(str(IMAGE_ROOT_PATH), filename)


@devices_bp.route("/api/images/upload", methods=["POST"])
def upload_image():
    """Accepts ip from form/query/json."""
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
            "image_url": build_public_url(save_name),
            "get_url_static": f"/images/{save_name}",
            "get_url_api": f"/api/images/{ip}",
            "get_url_api_exact": f"/api/images/{ip}?format={tag}",
        })

    except ValueError as ve:
        return jsonify({"ok": False, "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": "Upload failed", "error": str(e)}), 500


@devices_bp.route("/api/images/<ip>", methods=["GET"])
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


@devices_bp.route("/api/images/status/<ip>", methods=["GET"])
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


@devices_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "image_root_disk": str(IMAGE_ROOT_PATH),
        "base_image_api": BASE_IMAGE_API
    })


@devices_bp.route("/api/print-serial", methods=["POST"])
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
