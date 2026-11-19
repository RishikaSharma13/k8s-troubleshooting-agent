"""Print the newest kube-prometheus-stack version published at least MIN_AGE_DAYS ago, from Helm's cached index.

Never install "latest": a newly published release can carry problems before anyone notices.
Usage (after `helm repo add prometheus-community ... && helm repo update prometheus-community`):
    python3 docs/monitoring/pick_chart_version.py [index-file]
"""
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

CHART, MIN_AGE_DAYS = "kube-prometheus-stack", 14


def versions(index_text: str, chart: str = CHART) -> list[tuple[datetime, str]]:
    """(created, version) of each release of `chart` in a Helm repository index."""
    # the chart's section ends at the next chart name ("  other-chart:"), not at its own entries ("  - ...")
    m = re.search(rf"^  {re.escape(chart)}:\n(.*?)(?=^  [^\s-][^\n]*:\n|\Z)", index_text, re.S | re.M)
    out = []
    for entry in re.split(r"^  - ", m.group(1), flags=re.M)[1:] if m else []:
        created = re.search(r'^    created: "?([0-9T:.\-+Z]+)"?', "    " + entry, re.M)
        version = re.search(r"^    version: (\S+)", "    " + entry, re.M)
        if created and version:
            out.append((datetime.fromisoformat(created.group(1).replace("Z", "+00:00")), version.group(1)))
    return out


def pick(index_text: str, now: datetime, min_age_days: int = MIN_AGE_DAYS) -> tuple[str, datetime]:
    old_enough = [(c, v) for c, v in versions(index_text) if c <= now - timedelta(days=min_age_days)]
    if not old_enough:
        raise SystemExit("no version old enough")
    created, version = max(old_enough)
    return version, created


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else next(iter(glob.glob(os.path.expanduser(
        "~/.cache/helm/repository/prometheus-community-index.yaml"))), None)
    if not path:
        raise SystemExit("index not found: run helm repo add/update first, or pass the index file")
    v, c = pick(open(path).read(), datetime.now(timezone.utc))
    print(v)
    print(f"(published {c:%Y-%m-%d}, at least {MIN_AGE_DAYS} days ago)", file=sys.stderr)
