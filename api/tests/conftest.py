import psycopg
import pytest

import db
import proof


@pytest.fixture(scope="session")
def db_url():
    try:
        url = proof.fresh_db("offpeak_test")
    except psycopg.OperationalError as e:
        pytest.skip(f"no Postgres ({e})")
    db.URL = url
    return url


@pytest.fixture
def fresh(db_url):
    with psycopg.connect(db_url) as c:
        c.execute("TRUNCATE quotes, intervals, dq_issues, jobs, meter_files")
    return db_url


@pytest.fixture
def client(fresh):
    from fastapi.testclient import TestClient

    import app

    with TestClient(app.app) as c:
        yield c
