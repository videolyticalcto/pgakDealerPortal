"""
Shared helper / utility functions extracted from main.py.
"""

import json
import os
import random
import re
import string
import ipaddress
from typing import Optional
from urllib.parse import urljoin

from flask import request

from app.extensions import pwd_context, SNAPSHOT_DIR_MAP


# ── Unique code generation ──────────────────────────────────────────────────

def generate_unique_code(user_type):
    code = ''.join(random.choices(string.digits, k=5))
    return code


_VALID_CODE_COLUMNS = {'dealer_code', 'distributor_code'}


def code_exists_in_db(code, code_column, cur):
    """Check if code already exists in the database"""
    if code_column not in _VALID_CODE_COLUMNS:
        raise ValueError(f"Invalid code_column: {code_column}")

    if code_column == 'dealer_code':
        cur.execute("SELECT 1 FROM user_signups WHERE dealer_code = %s LIMIT 1", (code,))
    else:
        cur.execute("SELECT 1 FROM user_signups WHERE distributor_code = %s LIMIT 1", (code,))
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
    code_column = 'dealer_code' if user_type == 'dealer' else 'distributor_code'

    for attempt in range(max_attempts):
        code = generate_unique_code(user_type)
        if not code_exists_in_db(code, code_column, cur):
            return code

    return None


# ── Password validation ───────────────────────────────────────────────────

def validate_password(password: str) -> str | None:
    """
    Validate password strength. Returns error message or None if valid.
    Requirements: min 8 chars, 1 uppercase, 1 lowercase, 1 digit, 1 special char.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':\"\\|,.<>\/?`~]', password):
        return "Password must contain at least one special character"
    return None


# ── Password verification ──────────────────────────────────────────────────

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


# ── Snapshot dir helpers ───────────────────────────────────────────────────

def load_snapshot_dir_map():
    """
    Load SNAPSHOT_DIR_MAP from .env file.
    Expected format: JSON string

    Example in .env:
    SNAPSHOT_DIR_MAP={"192.168.1.44": "\\\\192.168.1.44\\SharedFolder", "192.168.1.111": "\\\\192.168.1.111\\pi4\\SharedFolder"}
    """
    snapshot_dir_json = os.getenv("SNAPSHOT_DIR_MAP", "{}")

    try:
        result = json.loads(snapshot_dir_json)
        print(f"[OK] Loaded SNAPSHOT_DIR_MAP from .env with {len(result)} entries")
        for ip, path in result.items():
            print(f"   {ip} -> {path}")
        return result
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in SNAPSHOT_DIR_MAP environment variable")
        print(f"   Error: {e}")
        print(f"   Using empty mapping as fallback")
        return {}


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
        print(f"   SNAPSHOT_DIR selected for {target_ip}: {snapshot_dir}")
    else:
        print(f"   WARNING: No SNAPSHOT_DIR configured for {target_ip}")

    return snapshot_dir


# ── IP extraction ──────────────────────────────────────────────────────────

def _extract_first_ip(ip_dict_or_value) -> Optional[str]:
    """Extract a usable (non-loopback) IP from the "IP Address" payload.

    Your agents usually send "IP Address" as a dict like:
      {"lo": "127.0.0.1", "eth0": "192.168.1.111"}

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


# ── URL / network helpers ─────────────────────────────────────────────────

def normalize_agent_url(agent_scheme: str, agent_ip: str, agent_port: int, path: str) -> str:
    base = f"{agent_scheme}://{agent_ip}:{agent_port}/"
    return urljoin(base, path.lstrip("/"))


# ── Email validation ──────────────────────────────────────────────────────

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

def is_valid_email(email: str) -> bool:
    return bool(email and EMAIL_REGEX.match(email))


# ── Request helpers ───────────────────────────────────────────────────────

def get_remote_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)
