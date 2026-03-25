import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "pgak-fixed-secret-key-change-this-in-production-2024")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

    # Database
    DB_CONFIG = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "pgak_db"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", "qwer1234")
    }

    # Pratap (face_recog_new) Database
    PRATAP_DB_CONFIG = {
        "host": os.getenv("PRATAP_DB_HOST", "20.40.46.161"),
        "port": int(os.getenv("PRATAP_DB_PORT", "5432")),
        "dbname": os.getenv("PRATAP_DB_NAME", "face_recog_new"),
        "user": os.getenv("PRATAP_DB_USER", "postgres"),
        "password": os.getenv("PRATAP_DB_PASSWORD", "qwer1234")
    }

    # Server
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0").strip()
    SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))

    # Agent defaults
    DEFAULT_AGENT_SCHEME = os.getenv("DEFAULT_AGENT_SCHEME", "https").strip().lower()
    DEFAULT_AGENT_PORT = int(os.getenv("DEFAULT_AGENT_PORT", "5001"))
    HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", "20"))

    # Socket Hub
    SOCKET_HUB_BIND = os.getenv("SOCKET_HUB_BIND", "0.0.0.0").strip() or "0.0.0.0"
    SOCKET_HUB_PORT = int(os.getenv("SOCKET_HUB_PORT", "5006"))
    SOCKET_HUB_READ_TIMEOUT = int(os.getenv("SOCKET_HUB_READ_TIMEOUT", "120"))
    SOCKET_HUB_DEFAULT_CMD_TIMEOUT = int(os.getenv("SOCKET_HUB_DEFAULT_CMD_TIMEOUT", "45"))

    # SMTP
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.pgak.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
    SMTP_TLS_SERVER_NAME = os.getenv("SMTP_TLS_SERVER_NAME", "")
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")

    # Image
    BASE_IMAGE_API = os.getenv("BASE_IMAGE_API", "")

    # Offline timeout
    OFFLINE_TIMEOUT = 30
