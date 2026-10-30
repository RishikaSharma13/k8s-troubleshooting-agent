"""The FastAPI application loads and serves the expected routes (guards the clean-up of unused modules)."""
from app.main import app


def test_app_loads_with_expected_routes():
    paths = {r.path for r in app.routes}
    assert "/health" in paths
    for path in ("/api/v1/actions/approve", "/api/v1/actions/reject", "/api/v1/remediation/execute",
                 "/api/v1/risks/pending-incidents"):
        assert path in paths, path
