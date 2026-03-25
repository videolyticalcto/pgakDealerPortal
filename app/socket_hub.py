"""
SOCKET HUB (Reverse connection from local agents -> Azure server)

Why: Azure server CANNOT reach private LAN IPs like 192.168.x.x. Agents must connect out.
Protocol: newline-delimited JSON (one JSON object per line).
Agent sends: {"type":"hello","agent_id":"192.168.1.111","hostname":"pi","agent_ip":"192.168.1.111"}
Server sends: {"type":"command","request_id":"...","command":"scan_with_credentials","data":{...}}
Agent replies: {"type":"response","request_id":"...","ok":true,"payload":{...}}
"""

import json
import socket
import threading
import time
import uuid

from app.config import Config

# ── Module-level state ──────────────────────────────────────────────────────

_SOCKET_AGENT_LOCK = threading.Lock()
_SOCKET_AGENT_CONNS = {}   # agent_id -> {"sock": socket.socket, "wlock": Lock, "last_seen": float, ...}

_PENDING_LOCK = threading.Lock()
_PENDING = {}  # request_id -> {"event": Event, "resp": dict or None}


# ── Internal helpers ────────────────────────────────────────────────────────

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


# ── Public API ──────────────────────────────────────────────────────────────

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
    print(f"   [SOCKET_HUB] Sending to {agent_id}: command={command}, data keys={list((data or {}).keys())}")
    print(f"   [SOCKET_HUB] Full payload: {cmd_payload}")

    try:
        _sock_send_json_line(sock_obj, wlock, cmd_payload)
        print(f"   [SOCKET_HUB] Command sent successfully to {agent_id}")
    except Exception as e:
        with _PENDING_LOCK:
            _PENDING.pop(req_id, None)
        return {"ok": False, "error": "failed to send command: %s" % e}

    if timeout is None:
        timeout = Config.SOCKET_HUB_DEFAULT_CMD_TIMEOUT

    ok = evt.wait(timeout)
    with _PENDING_LOCK:
        item = _PENDING.pop(req_id, None)

    if (not ok) or (not item) or (item.get("resp") is None):
        return {"ok": False, "error": "timeout waiting response from agent %s (cmd=%s)" % (agent_id, command)}

    return item["resp"]


# ── Client reader (runs in its own thread per connection) ───────────────────

def _socket_client_reader(sock_obj, addr):
    agent_id = None
    try:
        sock_obj.settimeout(Config.SOCKET_HUB_READ_TIMEOUT)
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


# ── Accept loop & startup ──────────────────────────────────────────────────

def _socket_hub_accept_loop():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((Config.SOCKET_HUB_BIND, Config.SOCKET_HUB_PORT))
    srv.listen(200)
    print("Socket hub listening on %s:%s" % (Config.SOCKET_HUB_BIND, Config.SOCKET_HUB_PORT))

    while True:
        try:
            client_sock, addr = srv.accept()
            t = threading.Thread(target=_socket_client_reader, args=(client_sock, addr), daemon=True)
            t.start()
        except Exception as e:
            print("Socket hub accept error:", e)
            time.sleep(1)


def start_socket_hub():
    threading.Thread(target=_socket_hub_accept_loop, daemon=True).start()
