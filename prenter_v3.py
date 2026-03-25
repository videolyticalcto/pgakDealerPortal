import argparse
import csv
import json
import os
import sys
import socket
from typing import Dict, List, Optional, Tuple

import psycopg2


# ============================
# Postgres registry (atomic ID generation)
# ============================
class PGAssetRegistry:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def init_schema(self) -> None:
        """
        Run this ONLY with an admin user (postgres) when tables don't exist.
        """
        schema_sql = """
        CREATE TABLE IF NOT EXISTS asset_counters (
            prefix TEXT PRIMARY KEY,
            next_num BIGINT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
            id BIGSERIAL PRIMARY KEY,
            asset_code TEXT UNIQUE NOT NULL,
            prefix TEXT NOT NULL,
            num BIGINT NOT NULL,
            device_name TEXT,
            serial TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            extra JSONB NOT NULL DEFAULT '{}'::jsonb
        );

        CREATE INDEX IF NOT EXISTS idx_assets_prefix_num ON assets(prefix, num);
        CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at);
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()

    def tables_exist(self) -> Tuple[bool, List[str]]:
        """
        Check required tables exist in public schema.
        """
        missing = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.asset_counters');")
                if cur.fetchone()[0] is None:
                    missing.append("public.asset_counters")

                cur.execute("SELECT to_regclass('public.assets');")
                if cur.fetchone()[0] is None:
                    missing.append("public.assets")

        return (len(missing) == 0, missing)

    def check_db(self) -> None:
        """
        Quick diagnostics: connection + table visibility + simple SELECT/INSERT permission test (rolled back).
        """
        ok, missing = self.tables_exist()
        if not ok:
            print("[ERR] Required tables are missing:", ", ".join(missing))
            print("Run with admin once: --init-schema (using postgres user), OR create tables manually in pgAdmin.")
            return

        # Permission checks (safe: we rollback)
        with self._connect() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    # basic SELECT
                    cur.execute("SELECT COUNT(*) FROM public.assets;")
                    _ = cur.fetchone()[0]

                    # try writing (counter) but rollback
                    cur.execute("SAVEPOINT sp;")
                    cur.execute(
                        """
                        INSERT INTO public.asset_counters(prefix, next_num)
                        VALUES ('__PERM_TEST__', 1)
                        ON CONFLICT(prefix) DO UPDATE SET next_num = public.asset_counters.next_num + 1;
                        """
                    )
                    cur.execute("ROLLBACK TO SAVEPOINT sp;")

                conn.rollback()
                print("[OK] Done DB check OK: tables exist + read/write permissions look good.")
            except Exception as e:
                conn.rollback()
                print("[ERR] DB check failed (permissions or connectivity issue). Error:")
                print(str(e))

    def reserve_codes(self, prefix: str, count: int, digits: int) -> List[Tuple[str, int]]:
        """
        Atomic allocation:
        - Stores `next_num` as "next number to allocate".
        - For first time: next_num becomes 1+count, and allocated start = 1.
        - For next time: next_num increases by count; allocated start = old_next_num.
        This is concurrency-safe even if multiple PCs run at the same time.
        """
        if count <= 0:
            return []

        with self._connect() as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                # IMPORTANT: increment by count (not count+1) on subsequent calls.
                cur.execute(
                    """
                    INSERT INTO public.asset_counters(prefix, next_num)
                    VALUES (%s, 1 + %s)
                    ON CONFLICT(prefix)
                    DO UPDATE SET next_num = public.asset_counters.next_num + %s
                    RETURNING next_num;
                    """,
                    (prefix, count, count),
                )
                new_next_num = cur.fetchone()[0]  # next number after reserving
            conn.commit()

        start = new_next_num - count
        codes = []
        for n in range(start, start + count):
            code = f"{prefix}-{n:0{digits}d}"
            codes.append((code, n))
        return codes

    def insert_asset(
        self,
        asset_code: str,
        prefix: str,
        num: int,
        device_name: Optional[str] = None,
        serial: Optional[str] = None,
        extra: Optional[Dict] = None,
    ) -> None:
        extra = extra or {}
        with self._connect() as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.assets(asset_code, prefix, num, device_name, serial, extra)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT(asset_code) DO NOTHING;
                    """,
                    (asset_code, prefix, num, device_name, serial, json.dumps(extra)),
                )
            conn.commit()


# ============================
# TSPL label building
# ============================
def _tspl_escape(s: str) -> str:
    return (s or "").replace('"', "'").strip()

def build_tspl_label(
    *,
    label_w_mm: float,
    label_h_mm: float,
    gap_mm: float,
    speed: int,
    density: int,
    direction: int,
    qr_x: int,
    qr_y: int,
    qr_ecc: str,
    qr_cell: int,
    qr_mode: str,
    qr_rotation: int,
    qr_model: str,
    qr_mask: str,
    qr_content: str,
    text_lines: List[Tuple[int, int, str]],
    text_font: str,
    text_rotation: int,
    text_xmul: int,
    text_ymul: int,
) -> bytes:
    code = _tspl_escape(qr_content)

    # 1. Setup
    cmds = [
        f"SIZE {label_w_mm} mm, {label_h_mm} mm",
        f"GAP {gap_mm} mm, 0 mm",
        f"SPEED {speed}",
        f"DENSITY {density}",
        f"DIRECTION {direction},0",
        "REFERENCE 0,0",
        "OFFSET 0 mm",
        "SET PEEL OFF",
        "SET CUTTER OFF",
        "SET PARTIAL_CUTTER OFF",
        "SET TEAR ON",
        "CLS",
        "CODEPAGE 1252",
    ]

    # 2. QR Code
    # QRCODE x,y,ECC,cell_width,mode,rotation,model,mask,"content"
    # Note: The original code used hardcoded values. We now use the args.
    # If the user didn't change defaults, it's roughly the same.
    cmds.append(
        f'QRCODE {qr_x},{qr_y},{qr_ecc},{qr_cell},{qr_mode},{qr_rotation},{qr_model},{qr_mask},"{code}"'
    )

    # 3. Text lines
    # TEXT x,y,"font",rotation,x_mul,y_mul,"content"
    # We iterate over the lines passed in from the main loop
    for (tx, ty, tcontent) in text_lines:
        esc_text = _tspl_escape(tcontent)
        cmds.append(
            f'TEXT {tx},{ty},"{text_font}",{text_rotation},{text_xmul},{text_ymul},"{esc_text}"'
        )

    # 4. Print
    cmds.append("PRINT 1,1")
    cmds.append("")  # trailing newline

    tspl_str = "\n".join(cmds)
    return tspl_str.encode("ascii", errors="replace")



# ============================
# Windows RAW printing
# ============================
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
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("TSC QR Labels", None, "RAW"))
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

# ============================
# Helpers
# ============================
def load_csv_rows(csv_path: str) -> List[Dict[str, str]]:
    rows = []
    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()})
    return rows




def mm_to_dots(mm: float, dpi: int) -> int:
    # Convert millimeters to printer dots.
    # 25.4 mm per inch; dots_per_mm = dpi/25.4
    try:
        mm_f = float(mm)
    except Exception:
        mm_f = 0.0
    return int(round((mm_f * float(dpi)) / 25.4))


def _qr_version_modules_for_bytes_len(data_len: int, ecc: str) -> Tuple[int, int]:
    """Best-effort QR version/modules estimator for centering.

    We estimate *byte-mode* capacity by version and ECC (L/M/Q/H) and pick
    the smallest version that fits `data_len`.

    Returns: (version, modules_count)
    modules_count = 21 + 4*(version-1)

    Note:
    - This is an approximation. If you want perfect centering for any payload,
      install `qrcode` (pip install qrcode) and we will use it when available.
    """

    ecc = (ecc or "M").upper().strip()
    if ecc not in {"L", "M", "Q", "H"}:
        ecc = "M"

    # Byte-mode capacities for versions 1..10 (enough for typical serials/URLs)
    # Source: standard QR capacity tables (byte mode)
    caps = {
        "L": [17, 32, 53, 78, 106, 134, 154, 192, 230, 271],
        "M": [14, 26, 42, 62, 84, 106, 122, 152, 180, 213],
        "Q": [11, 20, 32, 46, 60, 74, 86, 108, 130, 151],
        "H": [7, 14, 24, 34, 44, 58, 64, 84, 98, 119],
    }

    lst = caps[ecc]
    ver = 10
    for i, cap in enumerate(lst, start=1):
        if data_len <= cap:
            ver = i
            break

    modules = 21 + 4 * (ver - 1)
    return ver, modules


def estimate_qr_size_dots(qr_content: str, ecc: str, cell: int, quiet_modules: int = 4) -> int:
    """Estimate QR printed square size (dots) for centering.

    We prefer using `qrcode` library if installed to determine the exact
    version/modules for the payload. Otherwise, we fallback to a byte-mode
    capacity heuristic.

    Total modules includes quiet zone on both sides.
    """

    content = qr_content or ""
    # Try exact size using qrcode if available
    try:
        import qrcode

        # Map TSPL ECC to qrcode constants
        ecc_map = {
            "L": qrcode.constants.ERROR_CORRECT_L,
            "M": qrcode.constants.ERROR_CORRECT_M,
            "Q": qrcode.constants.ERROR_CORRECT_Q,
            "H": qrcode.constants.ERROR_CORRECT_H,
        }
        q = qrcode.QRCode(
            version=None,
            error_correction=ecc_map.get((ecc or "M").upper(), qrcode.constants.ERROR_CORRECT_M),
            box_size=1,
            border=quiet_modules,
        )
        q.add_data(content)
        q.make(fit=True)

        # `modules_count` already excludes border; border handled separately
        modules = getattr(q, "modules_count", None)
        if modules is None:
            m = q.get_matrix()
            modules = len(m) if m else 21

        total_modules = int(modules) + (2 * int(quiet_modules))
        return int(total_modules) * int(cell)
    except Exception:
        # Fallback heuristic
        _, modules = _qr_version_modules_for_bytes_len(len(content.encode("utf-8")), ecc)
        total_modules = int(modules) + (2 * int(quiet_modules))
        return int(total_modules) * int(cell)

# ============================
# Main
# ============================
def main():
    ap = argparse.ArgumentParser(description="TSC TSPL QR label printing with PostgreSQL registry (Windows).")

    # PostgreSQL connection
    ap.add_argument("--pg-host", default="localhost")
    ap.add_argument("--pg-port", default="5432")
    ap.add_argument("--pg-db", default="VMS_DB")
    ap.add_argument("--pg-user", default="postgres")
    ap.add_argument("--pg-pass", default="qwer1234")

    # One-time actions
    ap.add_argument("--init-schema", action="store_true", help="Create tables/indexes (run as admin postgres user).")
    ap.add_argument("--check-db", action="store_true", help="Check tables + permissions and exit.")
    ap.add_argument("--list-printers", action="store_true", help="List installed printers and exit.")


    # Print mode
    ap.add_argument(
        "--mode",
        choices=["asset", "serial"],
        default="asset",
        help="asset: generate asset codes + store to DB; serial: print provided serial (no DB).",
    )
    ap.add_argument("--serial", default="", help="Serial number to print when --mode serial.")
    ap.add_argument("--hostname", default="", help="Optional hostname to print when --mode serial.")

    # DPI + optional mm -> dots coordinate conversion (for easier alignment)
    ap.add_argument("--dpi", type=int, default=203, help="Printer DPI (usually 203 or 300).")
    ap.add_argument("--qr-x-mm", type=float, default=None, help="QR X position in mm (overrides --qr-x)")
    ap.add_argument("--qr-y-mm", type=float, default=None, help="QR Y position in mm (overrides --qr-y)")
    ap.add_argument("--text-x-mm", type=float, default=None, help="Text X position in mm (overrides --text-x)")
    ap.add_argument("--text-y-mm", type=float, default=None, help="Text Y position in mm (overrides --text-y)")
    ap.add_argument(
        "--text-line-gap-mm", type=float, default=None, help="Text line gap in mm (overrides --text-line-gap)"
    )

    ap.add_argument("--prefix", default="DEV")
    ap.add_argument("--digits", type=int, default=6)

    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--csv", default="")

    ap.add_argument("--qr-template", default="{code}", help='Example: "{code}" or "https://intranet/devices/{code}"')

    # Label settings
    ap.add_argument("--label-w-mm", type=float, default=50.0)
    ap.add_argument("--label-h-mm", type=float, default=30.0)
    ap.add_argument("--gap-mm", type=float, default=2.0)
    ap.add_argument("--speed", type=int, default=4)
    ap.add_argument("--density", type=int, default=8)
    ap.add_argument("--direction", type=int, default=1)

    # QR placement
    ap.add_argument("--qr-x", type=int, default=16)
    ap.add_argument("--qr-y", type=int, default=16)
    ap.add_argument(
        "--center-qr",
        action="store_true",
        help="Auto-center the QR within the label (overrides --qr-x/--qr-y unless you set --qr-x-mm/--qr-y-mm and do not use --center-qr).",
    )
    ap.add_argument(
        "--qr-quiet",
        type=int,
        default=4,
        help="QR quiet-zone modules used for QR-size estimation during centering (default: 4).",
    )
    ap.add_argument("--qr-ecc", default="M")
    ap.add_argument("--qr-cell", type=int, default=4)
    ap.add_argument("--qr-mode", default="A")
    ap.add_argument("--qr-rotation", type=int, default=0)
    ap.add_argument("--qr-model", default="M2")
    ap.add_argument("--qr-mask", default="S7")

    # Text
    ap.add_argument("--text-font", default="3")
    ap.add_argument("--text-rotation", type=int, default=0)
    ap.add_argument("--text-xmul", type=int, default=1)
    ap.add_argument("--text-ymul", type=int, default=1)
    ap.add_argument("--text-x", type=int, default=550)
    ap.add_argument("--text-y", type=int, default=250)
    ap.add_argument("--text-line-gap", type=int, default=30)
    ap.add_argument("--company", default="My Company")
    ap.add_argument(
        "--only-qr",
        action="store_true",
        help="Print ONLY the QR code (no text at all). Useful for small 60x30 labels.",
    )
    ap.add_argument(
        "--only-asset-text",
        action="store_true",
        help="Print ONLY the asset code text beside the QR (no company, device name, or serial).",
    )

    # Printer target
    ap.add_argument("--printer", default="")
    ap.add_argument("--net-ip", default="")
    ap.add_argument("--net-port", type=int, default=9100)

    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Convert optional mm positions to dots (override the dot-based args)
    if args.qr_x_mm is not None:
        args.qr_x = mm_to_dots(args.qr_x_mm, args.dpi)
    if args.qr_y_mm is not None:
        args.qr_y = mm_to_dots(args.qr_y_mm, args.dpi)
    if args.text_x_mm is not None:
        args.text_x = mm_to_dots(args.text_x_mm, args.dpi)
    if args.text_y_mm is not None:
        args.text_y = mm_to_dots(args.text_y_mm, args.dpi)
    if args.text_line_gap_mm is not None:
        args.text_line_gap = mm_to_dots(args.text_line_gap_mm, args.dpi)

    if args.list_printers:
        printers = list_windows_printers()
        if not printers:
            print("No printers found OR pywin32 missing. Install: pip install pywin32")
            return
        print("Installed printers:")
        for p in printers:
            print(" -", p)

        return


    # ============================
    # SERIAL MODE (NO DB)
    # ============================
    if args.mode == "serial":
        serial_val = (args.serial or "").strip()
        if not serial_val:
            print("Provide --serial when --mode serial", file=sys.stderr)
            sys.exit(1)

        count = args.count if (args.count and args.count > 0) else 1

        # In serial mode, hide default company line unless user explicitly changed it
        if args.company == "My Company":
            args.company = ""

        label_bytes_list: List[bytes] = []

        for _ in range(count):
            # QR payload: if template has {code}, replace it with serial; otherwise use template as-is
            if "{code}" in (args.qr_template or ""):
                qr_payload = args.qr_template.format(code=serial_val)
            else:
                qr_payload = (args.qr_template or serial_val)

            # Optional: auto-center QR (computed from label size + estimated QR size)
            qr_x = args.qr_x
            qr_y = args.qr_y
            if args.center_qr:
                lw = mm_to_dots(args.label_w_mm, args.dpi)
                lh = mm_to_dots(args.label_h_mm, args.dpi)
                qs = estimate_qr_size_dots(qr_payload, args.qr_ecc, args.qr_cell, quiet_modules=args.qr_quiet)
                qr_x = max(0, int((lw - qs) // 2))
                qr_y = max(0, int((lh - qs) // 2))

            # Label text lines
            lines: List[Tuple[int, int, str]] = []
            if not args.only_qr:
                y = args.text_y
                if args.company:
                    lines.append((args.text_x, y, args.company))
                    y += args.text_line_gap
                if args.hostname:
                    lines.append((args.text_x, y, f"HOST: {args.hostname[:18]}"))
                    y += args.text_line_gap
                lines.append((args.text_x, y, f"SN: {serial_val[:18]}"))

            tspl = build_tspl_label(
                label_w_mm=args.label_w_mm,
                label_h_mm=args.label_h_mm,
                gap_mm=args.gap_mm,
                speed=args.speed,
                density=args.density,
                direction=args.direction,
                qr_x=qr_x,
                qr_y=qr_y,
                qr_ecc=args.qr_ecc,
                qr_cell=args.qr_cell,
                qr_mode=args.qr_mode,
                qr_rotation=args.qr_rotation,
                qr_model=args.qr_model,
                qr_mask=args.qr_mask,
                qr_content=qr_payload,
                text_lines=lines,
                text_font=args.text_font,
                text_rotation=args.text_rotation,
                text_xmul=args.text_xmul,
                text_ymul=args.text_ymul,
            )
            label_bytes_list.append(tspl)

        job_bytes = b"".join(label_bytes_list)

        if args.dry_run:
            print(job_bytes.decode("ascii", errors="replace"))
            print(f"\nGenerated {count} label(s) (serial mode).")
            return

        # Print
        if args.net_ip:
            send_raw_to_network_printer(args.net_ip, args.net_port, job_bytes)
            print(f"Printed {count} label(s) via network to {args.net_ip}:{args.net_port}")
        else:
            if not args.printer:
                print('Provide --printer "Your TSC Printer Name" OR use --net-ip', file=sys.stderr)
                sys.exit(1)
            send_raw_to_windows_printer(args.printer, job_bytes)
            print(f'Printed {count} label(s) to Windows printer: "{args.printer}"')

        print("[OK] Done. Serial label(s) printed.")
        return

    dsn = (
        f"host={args.pg_host} port={args.pg_port} dbname={args.pg_db} "
        f"user={args.pg_user} password={args.pg_pass}"
    )
    registry = PGAssetRegistry(dsn)

    if args.init_schema:
        registry.init_schema()
        print("[OK] Done Schema initialized successfully.")
        return

    if args.check_db:
        registry.check_db()
        return

    # Ensure tables exist (but DO NOT try to create them here)
    ok, missing = registry.tables_exist()
    if not ok:
        print("[ERR] Required tables missing:", ", ".join(missing), file=sys.stderr)
        print("Run either:")
        print("  1) Create tables manually in pgAdmin, OR")
        print("  2) Run: python .\\tsc_qr_label_printer_postgres.py --init-schema --pg-user postgres --pg-pass <pass> ...")
        sys.exit(1)

    # Decide input mode
    csv_rows = []
    if args.csv:
        if not os.path.exists(args.csv):
            print(f"CSV not found: {args.csv}", file=sys.stderr)
            sys.exit(1)
        csv_rows = load_csv_rows(args.csv)
        if not csv_rows:
            print("CSV has no rows.", file=sys.stderr)
            sys.exit(1)
        count = len(csv_rows)
    else:
        if args.count <= 0:
            print("Provide --count N OR --csv devices.csv", file=sys.stderr)
            sys.exit(1)
        count = args.count

    reserved = registry.reserve_codes(args.prefix, count, args.digits)

    label_bytes_list: List[bytes] = []

    for i, (code, num) in enumerate(reserved):
        device_name = None
        serial = None
        extra = {}

        if csv_rows:
            row = csv_rows[i]
            device_name = row.get("device_name") or row.get("name") or ""
            serial = row.get("serial") or row.get("serial_no") or row.get("sn") or ""
            extra = {k: v for k, v in row.items() if k not in {"device_name", "name", "serial", "serial_no", "sn"}}

        registry.insert_asset(
            asset_code=code,
            prefix=args.prefix,
            num=num,
            device_name=device_name or None,
            serial=serial or None,
            extra=extra or None,
        )

        qr_payload = args.qr_template.format(code=code)

        # Optional: auto-center QR (computed from label size + estimated QR size)
        qr_x = args.qr_x
        qr_y = args.qr_y
        if args.center_qr:
            lw = mm_to_dots(args.label_w_mm, args.dpi)
            lh = mm_to_dots(args.label_h_mm, args.dpi)
            qs = estimate_qr_size_dots(qr_payload, args.qr_ecc, args.qr_cell, quiet_modules=args.qr_quiet)
            qr_x = max(0, int((lw - qs) // 2))
            qr_y = max(0, int((lh - qs) // 2))

        # Label text lines
        lines: List[Tuple[int, int, str]] = []
        if not args.only_qr:
            if args.only_asset_text:
                # Print only the asset code text beside the QR
                lines.append((args.text_x, args.text_y, f"{code}"))
            else:
                # Print full details
                y = args.text_y
                if args.company:
                    lines.append((args.text_x, y, args.company))
                    y += args.text_line_gap
                lines.append((args.text_x, y, f"ASSET: {code}"))
                y += args.text_line_gap
                if device_name:
                    lines.append((args.text_x, y, f"NAME: {device_name[:18]}"))
                    y += args.text_line_gap
                if serial:
                    lines.append((args.text_x, y, f"SN: {serial[:18]}"))

        tspl = build_tspl_label(
            label_w_mm=args.label_w_mm,
            label_h_mm=args.label_h_mm,
            gap_mm=args.gap_mm,
            speed=args.speed,
            density=args.density,
            direction=args.direction,
            qr_x=qr_x,
            qr_y=qr_y,
            qr_ecc=args.qr_ecc,
            qr_cell=args.qr_cell,
            qr_mode=args.qr_mode,
            qr_rotation=args.qr_rotation,
            qr_model=args.qr_model,
            qr_mask=args.qr_mask,
            qr_content=qr_payload,
            text_lines=lines,
            text_font=args.text_font,
            text_rotation=args.text_rotation,
            text_xmul=args.text_xmul,
            text_ymul=args.text_ymul,
        )
        label_bytes_list.append(tspl)

    job_bytes = b"".join(label_bytes_list)

    if args.dry_run:
        print(job_bytes.decode("ascii", errors="replace"))
        print(f"\nGenerated {count} labels and inserted into PostgreSQL.")
        return

    # Print
    if args.net_ip:
        send_raw_to_network_printer(args.net_ip, args.net_port, job_bytes)
        print(f"Printed {count} labels via network to {args.net_ip}:{args.net_port}")
    else:
        if not args.printer:
            print('Provide --printer "Your TSC Printer Name" OR use --net-ip', file=sys.stderr)
            sys.exit(1)
        send_raw_to_windows_printer(args.printer, job_bytes)
        print(f'Printed {count} labels to Windows printer: "{args.printer}"')

    print("[OK] Done. Labels printed + records stored in PostgreSQL.")


if __name__ == "__main__":
    main()