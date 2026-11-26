"""Print the newest version of a chart published at least MIN_AGE_DAYS ago, from Helm's cached repository index.

Never install "latest": a newly published release can carry problems before anyone notices.
Usage (after `helm repo add <repo> <url> && helm repo update <repo>`):
    python3 docs/monitoring/pick_chart_version.py                                  # kube-prometheus-stack
    python3 docs/monitoring/pick_chart_version.py --repo argo --chart argo-cd       # Argo CD
    python3 docs/monitoring/pick_chart_version.py --chart <name> <index-file>
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

CHART, MIN_AGE_DAYS = "kube-prometheus-stack", 14


def parse_time(text: str) -> datetime:
    """Helm indexes write 1 to 9 decimals (trailing zeros dropped); Python 3.10 accepts exactly 3 or 6.
    Normalize the fraction to exactly 6 digits: trimmed if longer, padded with zeros if shorter."""
    text = text.replace("Z", "+00:00")
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    return datetime.fromisoformat(text)


def versions(index_text: str, chart: str = CHART) -> list[tuple[datetime, str]]:
    """(created, version) of each release of `chart` in a Helm repository index."""
    # the chart's section ends at the next chart name ("  other-chart:"), not at its own entries ("  - ...")
    m = re.search(rf"^  {re.escape(chart)}:\n(.*?)(?=^  [^\s-][^\n]*:\n|\Z)", index_text, re.S | re.M)
    out = []
    for entry in re.split(r"^  - ", m.group(1), flags=re.M)[1:] if m else []:
        created = re.search(r'^    created: "?([0-9T:.\-+Z]+)"?', "    " + entry, re.M)
        version = re.search(r"^    version: (\S+)", "    " + entry, re.M)
        if created and version:
            out.append((parse_time(created.group(1)), version.group(1)))
    return out


def pick(index_text: str, now: datetime, min_age_days: int = MIN_AGE_DAYS, chart: str = CHART) -> tuple[str, datetime]:
    old_enough = [(c, v) for c, v in versions(index_text, chart) if c <= now - timedelta(days=min_age_days)]
    if not old_enough:
        raise SystemExit("no version old enough")
    created, version = max(old_enough)
    return version, created


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chart", default=CHART)
    ap.add_argument("--repo", default="prometheus-community", help="the name used in `helm repo add`")
    ap.add_argument("index", nargs="?", help="index file (default: Helm's cache for --repo)")
    args = ap.parse_args()
    path = args.index or next(iter(glob.glob(os.path.expanduser(
        f"~/.cache/helm/repository/{args.repo}-index.yaml"))), None)
    if not path:
        raise SystemExit("index not found: run helm repo add/update first, or pass the index file")
    v, c = pick(open(path).read(), datetime.now(timezone.utc), chart=args.chart)
    print(v)
    print(f"({args.chart}: published {c:%Y-%m-%d}, at least {MIN_AGE_DAYS} days ago)", file=sys.stderr)
