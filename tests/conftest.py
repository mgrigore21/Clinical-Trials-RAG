"""Shared test setup: a throwaway database for the integration tests.

It is a separate database (`trials_test`) on the same Postgres server, so tests can empty and
refill their tables without ever touching the data you ingested into `trials`.
"""

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app import db
from app.config import Settings

TEST_DB = "trials_test"


@pytest.fixture(scope="session")
def test_db_url():
    base_url = Settings(_env_file=None).database_url
    try:
        admin = psycopg.connect(make_conninfo(base_url, dbname="postgres"), autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (start it with `make db-up`)")
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not exists.fetchone():
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    url = make_conninfo(base_url, dbname=TEST_DB)
    db.init_schema(url)
    return url


@pytest.fixture
def conn(test_db_url):
    connection = db.connect(test_db_url)
    connection.autocommit = True
    connection.execute("TRUNCATE trials CASCADE")  # every test starts from empty tables
    yield connection
    connection.close()
