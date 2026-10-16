"""Test-suite safety guard.

Tests that read or write the database run only when DATABASE_URL points at a
database whose name ends in "_test". Otherwise they are skipped, so a plain
`pytest` run can never modify the development database.
"""
import os

import pytest
from sqlalchemy.engine import make_url

# Mirrors the default in app/models/database.py, which uses the same variable.
DEFAULT_DATABASE_URL = "postgresql://postgres:password@localhost:5432/k8s_agent"

EFFECTIVE_DB_NAME = make_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)).database or ""
DB_IS_TEST = EFFECTIVE_DB_NAME.endswith("_test")

# Existing tests that use the database. New database tests use @pytest.mark.db instead.
EXISTING_DB_TESTS = (
    "test_database.py::",
    "test_predictor_api.py::",
    "test_predictor.py::TestRiskManager::",
    "test_predictor.py::TestMetricsCollectorIncidents::",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "db: uses the database; runs only against a *_test database"
    )


def pytest_report_header(config):
    state = "ENABLED" if DB_IS_TEST else "SKIPPED (not a *_test database)"
    return f"database tests: {state}; DATABASE_URL database = {EFFECTIVE_DB_NAME!r}"


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(
        reason=f"database test: DATABASE_URL must name a *_test database "
               f"(current: {EFFECTIVE_DB_NAME!r})"
    )
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if any(target in nodeid for target in EXISTING_DB_TESTS):
            item.add_marker(pytest.mark.db)
        if item.get_closest_marker("db") and not DB_IS_TEST:
            item.add_marker(skip_db)
