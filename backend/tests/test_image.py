"""The container image keeps its safety properties (static checks of Dockerfile and .dockerignore)."""
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text()


def test_every_stage_uses_the_python_version_ci_tests_on():
    bases = re.findall(r"^FROM (\S+)", DOCKERFILE, re.M)
    ci = re.search(r'python-version:\s*"?([0-9.]+)', (ROOT / ".github/workflows/tests.yml").read_text()).group(1)
    assert bases and all(b.startswith(f"python:{ci}-slim") for b in bases), (bases, ci)


def test_kubectl_is_pinned_and_checksum_verified():
    assert re.search(r"ARG KUBECTL_VERSION=v\d+\.\d+\.\d+", DOCKERFILE)
    assert "stable.txt" not in DOCKERFILE and "latest" not in DOCKERFILE
    assert "sha256sum --check" in DOCKERFILE


def test_final_image_runs_as_non_root():
    final = DOCKERFILE.split("# ---------- runtime ----------", 1)[1]
    users = re.findall(r"^USER (\S+)", final, re.M)
    assert users and not users[-1].startswith(("0", "root")), users


def test_runtime_image_has_no_test_tools():
    runtime = (ROOT / "backend/requirements.txt").read_text()
    dev = (ROOT / "backend/requirements-dev.txt").read_text()
    assert "pytest" not in runtime and "pytest" in dev and "-r requirements.txt" in dev


def test_build_context_is_an_allow_list():
    lines = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert lines[0] == "*" and "**/.env" in lines              # nothing is sent unless allowed; secrets never
    for needed in ("!alembic/", "!backend/app/", "!docs/results/"):
        assert needed in lines


def test_hadolint_is_clean():
    if not shutil.which("hadolint"):
        return
    assert subprocess.run(["hadolint", str(ROOT / "Dockerfile")], capture_output=True).returncode == 0
