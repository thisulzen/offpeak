import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

URL = os.environ.get("DATABASE_URL", "postgresql://offpeak:offpeak@localhost:5432/offpeak")


def connect(url=None):
    return psycopg.connect(url or URL, row_factory=dict_row, autocommit=True)


def migrate(conn):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(42)")  # api and worker start together
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
