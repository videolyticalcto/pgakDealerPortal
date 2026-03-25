import threading
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory stores
DEVICES = {}
DISCOVERED = {}

# Snapshot dir map (loaded at startup)
SNAPSHOT_DIR_MAP = {}
