"""Security exceptions and scanning stay explicit: every accepted finding has a reason and an expiry date."""
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_trivy_exception_has_a_reason_and_a_future_expiry():
    lines = (ROOT / ".trivyignore").read_text().splitlines()
    entries = [(i, l) for i, l in enumerate(lines) if l.strip() and not l.startswith("#")]
    assert entries, "no exceptions: delete the file rather than leave it empty"
    for i, line in entries:
        m = re.fullmatch(r"(\S+) exp:(\d{4}-\d{2}-\d{2})", line.strip())
        assert m, f"line {i + 1}: '<ID> exp:YYYY-MM-DD' expected, got {line!r}"
        assert date.fromisoformat(m.group(2)) > date.today(), f"{m.group(1)} has expired: review it"
        assert lines[i - 1].startswith("#") and len(lines[i - 1]) > 20, f"{m.group(1)} needs a written reason above it"


def test_ci_scans_with_a_pinned_verified_trivy():
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert re.search(r"TRIVY_VERSION: \d+\.\d+\.\d+", wf) and "sha256sum --check" in wf
    assert "trivy config" in wf and "--exit-code 1" in wf
    assert "--severity CRITICAL --ignore-unfixed" in wf               # the gate: fixable criticals fail the build


def _workflow():
    import yaml
    return yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())


def test_only_the_publish_job_may_write_and_only_packages():
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}                       # the default for every job
    writers = {name: job.get("permissions") for name, job in wf["jobs"].items()
               if "write" in (job.get("permissions") or {}).values()}
    assert writers == {"publish": {"contents": "read", "packages": "write"}}


def test_images_are_published_only_from_main_after_every_check():
    job = _workflow()["jobs"]["publish"]
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert set(job["needs"]) == {"test", "image"}
    steps = [s.get("name", "") for s in job["steps"]]
    gate = next(i for i, n in enumerate(steps) if "Vulnerability gate" in n)
    push = next(i for i, n in enumerate(steps) if n.startswith("Push"))
    assert gate < push                                                     # nothing unscanned is ever pushed
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "--password-stdin" in run and "latest" not in run                # no stored password; no mutable tag
