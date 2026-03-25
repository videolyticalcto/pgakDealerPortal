"""
Email utilities: SMTP config, send_email_otp, send_email.
"""

import os
import ssl
import smtplib
import traceback
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Config


# ── SMTP CONFIG dict (built from Config class) ─────────────────────────────

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
    "use_tls": _bool_env(os.getenv("SMTP_USE_TLS"), True),
    "debug": _bool_env(os.getenv("SMTP_DEBUG"), False),
    "tls_server_name": os.getenv("SMTP_TLS_SERVER_NAME", "").strip(),
    "skip_hostname_verify": _bool_env(os.getenv("SMTP_TLS_SKIP_HOSTNAME_VERIFY"), False),
}

if not SMTP_CONFIG["from_email"]:
    SMTP_CONFIG["from_email"] = SMTP_CONFIG["username"]


# ── Internal TLS helper ────────────────────────────────────────────────────

def _smtp_starttls_with_server_name(server: smtplib.SMTP, context: ssl.SSLContext, server_name: str):
    code, resp = server.docmd("STARTTLS")
    if code != 220:
        raise smtplib.SMTPResponseException(code, resp)

    # Wrap with SNI + hostname verification against server_name
    server.sock = context.wrap_socket(server.sock, server_hostname=server_name)
    server.file = server.sock.makefile("rb")

    # Reset SMTP state after TLS
    server.helo_resp = None
    server.ehlo_resp = None
    server.esmtp_features = {}
    server.does_esmtp = False
    server.ehlo()


# ── send_email_otp ─────────────────────────────────────────────────────────

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


# ── send_email (plain text) ────────────────────────────────────────────────

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
            _smtp_starttls_with_server_name(server, context, tls_server_name)

        # login only if username provided
        if username:
            server.login(username, password)

        server.send_message(msg)
