"""Components survive a database restart: dead pooled connections are replaced, not used."""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


@pytest.mark.db
def test_a_killed_pooled_connection_is_replaced_not_used():
    url = os.environ["DATABASE_URL"]
    admin = create_engine(url)
    outcomes = {}
    for pre_ping in (False, True):
        eng = create_engine(url, pool_pre_ping=pre_ping, pool_size=1, max_overflow=0)
        with eng.connect() as c:                                    # leave one connection idle in the pool
            pid = c.execute(text("SELECT pg_backend_pid()")).scalar()
        with admin.connect() as a:                                  # what a database restart does to it
            a.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            a.commit()
        try:
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            outcomes[pre_ping] = "ok"
        except OperationalError:
            outcomes[pre_ping] = "failed"
        eng.dispose()
    admin.dispose()
    assert outcomes == {False: "failed", True: "ok"}               # the test reproduces it, and the setting fixes it


def test_the_shared_engine_checks_connections_before_use():
    from app.models.database import engine
    assert engine.pool._pre_ping is True
