"""Tests for the realistic memory patterns (docs/testing/pattern-lab)."""
import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "docs" / "testing" / "pattern-lab"
spec = importlib.util.spec_from_file_location("patternload", ROOT / "patternload.py")
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


def first_crossing_min(mode, limit=256, seed=1, horizon_s=4 * 3600):
    rng, state = random.Random(seed), {}
    for t in range(0, horizon_s, 10):
        if pl.target_mib(mode, t, rng, state) > limit:
            return t / 60
    return None


def test_cycle_rises_and_falls_around_a_leak():
    rng, st = random.Random(0), {}
    v = lambda t: pl.target_mib("cycle", t, rng, st)
    assert v(0) == pytest.approx(40)
    assert v(300) == pytest.approx(40 + 10 + 30)            # a quarter of the 20-minute cycle: the peak
    assert v(900) == pytest.approx(40 + 30 - 30)            # three quarters: the trough
    assert v(900) < v(300)                                  # memory really goes down
    assert 70 <= first_crossing_min("cycle") <= 110         # several cycles per life


def test_steps_are_flat_between_jumps():
    rng, st = random.Random(0), {}
    v = lambda t: pl.target_mib("step", t, rng, st)
    assert v(0) == v(299) == 20 and v(300) == 45
    assert first_crossing_min("step") == pytest.approx(50)  # 20 + 25 x 10 > 256 at the 10th step


def test_noise_and_spikes():
    rng, st = random.Random(3), {}
    values = [pl.target_mib("noisy", t, rng, st) for t in range(0, 3600, 10)]
    spikes = sum(1 for a, b in zip(values, values[1:]) if b - a > 25)
    assert spikes >= 1                                      # occasional spikes happen
    for seed in range(5):
        assert 35 <= first_crossing_min("noisy", seed=seed) <= 75   # a spike can bring the crash forward


def test_freed_memory_is_returned_to_the_system():
    status = Path("/proc/self/status")
    if not status.exists():
        pytest.skip("needs Linux /proc")
    rss = lambda: int(next(l for l in status.read_text().splitlines() if l.startswith("VmRSS")).split()[1]) // 1024
    before = rss()
    blocks = [bytearray(b"\x01") * pl.MiB for _ in range(60)]
    grown = rss()
    del blocks
    assert grown - before >= 50                             # written blocks are resident
    assert rss() - before <= 10                             # and released when freed


def test_manifest_is_safe_and_matches_the_script():
    yaml = pytest.importorskip("yaml")
    docs = [d for d in yaml.safe_load_all((ROOT / "pattern-lab.yaml").read_text()) if d]
    ns = next(d for d in docs if d["kind"] == "Namespace")
    assert "reliability-agent/remediation" not in (ns["metadata"].get("labels") or {})   # actions refused here
    script = next(d for d in docs if d["kind"] == "ConfigMap")["data"]["patternload.py"]
    assert script.rstrip() == (ROOT / "patternload.py").read_text().rstrip()
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    assert set(deps) == {"cycle-leak", "noisy-leak", "step-leak"}
    for d in deps.values():
        c = d["spec"]["template"]["spec"]["containers"][0]
        assert c["resources"]["limits"]["memory"] == "256Mi"
