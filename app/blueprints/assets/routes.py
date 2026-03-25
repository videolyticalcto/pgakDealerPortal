"""
Assets Blueprint routes.

Handles: /api/assets/health, /api/assets/list-printers, /api/assets/check-db,
         /api/assets/generate-and-print, /api/assets/online-devices, /api/assets/latest
"""

import csv
import os
import socket
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import request, jsonify

from app.blueprints.assets import assets_bp
from app.config import Config
from app.extensions import DEVICES

PG_SSLMODE = os.getenv("PG_SSLMODE", "prefer")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _build_dsn() -> str:
    return (
        f"host={Config.DB_CONFIG['host']} port={Config.DB_CONFIG['port']} dbname={Config.DB_CONFIG['dbname']} "
        f"user={Config.DB_CONFIG['user']} password={Config.DB_CONFIG['password']} sslmode={PG_SSLMODE}"
    )


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
    Accept BOTH formats:
      A) {"rows":[{"serial":"..."}]}
      B) {"serial":"..."}  <-- dashboard sends this
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

    # Also allow form field "serial"
    if not rows:
        single_serial_form = (request.form.get("serial") or request.form.get("serial_no") or request.form.get("sn") or "").strip()
        if single_serial_form:
            rows = [{"serial": single_serial_form}]

    return rows


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


def check_device_online(device_hostname: str = "", device_ip: str = "") -> Tuple[bool, str]:
    """
    Returns: (is_online, reason_message)

    Priority:
    1) In-memory DEVICES dict (fast)
    2) DB fallback: device_status (latest row)
    3) DB fallback: system_information.status
    """
    device_hostname = (device_hostname or "").strip()
    device_ip = (device_ip or "").strip()

    # 1) DEVICES dict check
    DEV = DEVICES or {}

    if device_hostname and device_hostname in DEV:
        d = DEV.get(device_hostname) or {}
        st = (d.get("status") or "").upper()
        if st in ("ACTIVE", "ONLINE"):
            return True, f"ONLINE via DEVICES (hostname={device_hostname}, status={st})"
        return False, f"OFFLINE via DEVICES (hostname={device_hostname}, status={st})"

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

    # 2) DB fallback: device_status
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

    # 3) DB fallback: system_information.status
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

    def insert_asset_no_duplicate(self, serial: str, qr_status: str = "PENDING") -> bool:
        serial = (serial or "").strip()
        qr_status = (qr_status or "PENDING").strip().upper()

        if not serial:
            raise ValueError("serial is required")

        with self._connect() as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
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
        serial = (serial or "").strip()
        qr_status = (qr_status or "PENDING").strip().upper()

        if not serial:
            raise ValueError("serial is required")

        _ = self.insert_asset_no_duplicate(serial=serial, qr_status=qr_status)


# =============================================================================
# ROUTES
# =============================================================================

@assets_bp.route("/health", methods=["GET"])
def assets_health():
    return jsonify({
        "status": "success",
        "message": "Asset Label API is running",
        "server_time": datetime.now().isoformat()
    }), 200


@assets_bp.route("/list-printers", methods=["GET"])
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


@assets_bp.route("/check-db", methods=["GET"])
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


@assets_bp.route("/generate-and-print", methods=["POST"])
def api_generate_and_print():
    """
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

        inserted_serials: List[str] = []
        skipped_serials: List[str] = []
        already_printed_serials: List[str] = []
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
                continue

            inserted = registry.insert_asset_no_duplicate(serial=serial, qr_status=qr_status)
            if inserted:
                inserted_serials.append(serial)
            else:
                skipped_serials.append(serial)

            registry.insert_asset(serial=serial, qr_status=qr_status)

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

        # After SUCCESSFUL PRINT => update DB qr_status to PRINTED
        updated_to_printed = 0
        if printed and (not dry_run) and will_print_serials:
            try:
                updated_to_printed = registry.bulk_update_qr_status(will_print_serials, "PRINTED")
                for a in assets_out:
                    if (a.get("serial") in will_print_serials) and (not a.get("blocked")):
                        a["qr_status"] = "PRINTED"
            except Exception:
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


@assets_bp.route("/online-devices", methods=["GET"])
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


@assets_bp.route("/latest", methods=["GET"])
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
