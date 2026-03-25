import psycopg2
from psycopg2.extras import RealDictCursor
from app.config import Config

def get_db_conn():
    return psycopg2.connect(**Config.DB_CONFIG)

def get_db_cursor(conn, dict_cursor=False):
    if dict_cursor:
        return conn.cursor(cursor_factory=RealDictCursor)
    return conn.cursor()
