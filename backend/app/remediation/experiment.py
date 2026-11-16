"""Controlled experiment: act on every other predicted incident, leave the others alone.

Purpose: show that an approved action *causes* the avoided crash. Acted-on incidents ("treated")
should end AVOIDED; untouched ones ("control") should end CONFIRMED, showing the crash really
would have happened without the action.

Safety: this is a bounded batch approval started explicitly by a person (--confirm, a fixed
--count). Every action goes through the normal API (/actions/approve, then /remediation/execute),
so the approval gate, allowlist, namespace label, server-side dry run and recorded evidence all
apply exactly as for a manual approval. predict-lab (the evaluation's ground truth) is refused.

Every decision is appended to a JSON-lines log, so a run can be stopped and resumed, and
`summary` reports the outcome of each group once verification has labelled the incidents.

Usage:
    PYTHONPATH=backend python -m app.remediation.experiment run --confirm --count 10 \\
        --namespace remediate-lab --log experiment.jsonl [--action RESTART_DEPLOYMENT]
    PYTHONPATH=backend python -m app.remediation.experiment summary --log experiment.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from app.models.database import Execution, Incident, SessionLocal

PROTECTED_NAMESPACES = ("predict-lab",)
MIN_LEAD_S = 180          # only act when the predicted crash is at least 3 minutes away
MAX_COUNT = 50


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def http_post(api: str) -> Callable[[str, dict], dict]:
    import httpx

    def post(endpoint: str, params: dict) -> dict:
        r = httpx.post(f"{api}{endpoint}", params=params, timeout=240)
        return r.json()
    return post


class Experiment:
    def __init__(self, namespace: str, count: int, log: Path, post: Callable[[str, dict], dict],
                 action: str = "RESTART_DEPLOYMENT", session_factory: Callable = SessionLocal):
        if namespace in PROTECTED_NAMESPACES:
            raise ValueError(f"{namespace} holds the evaluation's ground truth; experiments may not act on it")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"--count must be between 1 and {MAX_COUNT}")
        self.namespace, self.count, self.log, self.post = namespace, count, log, post
        self.action, self.session_factory = action, session_factory
        self.entries = read_log(log)          # resume a stopped run

    def _seen(self) -> set[str]:
        return {e["incident_id"] for e in self.entries}

    def counts(self) -> dict[str, int]:
        c = {"treated": 0, "control": 0}
        for e in self.entries:
            c[e["group"]] += 1
        return c

    def done(self) -> bool:
        c = self.counts()
        return c["treated"] >= self.count and c["control"] >= self.count

    def _append(self, entry: dict) -> None:
        self.entries.append(entry)
        with self.log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def candidates(self, now: datetime) -> list[tuple[str, datetime]]:
        db = self.session_factory()
        try:
            rows = (db.query(Incident.id, Incident.predicted_failure_at)
                    .filter(Incident.source == "forecast", Incident.namespace == self.namespace,
                            Incident.status == "PENDING",
                            Incident.predicted_failure_at > now + timedelta(seconds=MIN_LEAD_S))
                    .order_by(Incident.created_at).all())
        finally:
            db.close()
        return [(i, p) for i, p in rows if i not in self._seen()]

    def step(self, now: Optional[datetime] = None) -> list[dict]:
        """Assign each new incident alternately to treated / control; act on treated ones."""
        now = now or datetime.now(timezone.utc)
        made = []
        for incident_id, predicted in self.candidates(now):
            if self.done():
                break
            c = self.counts()
            group = "treated" if c["treated"] <= c["control"] and c["treated"] < self.count else "control"
            if group == "control" and c["control"] >= self.count:
                group = "treated"
            entry = {"incident_id": incident_id, "group": group, "assigned_at": now.isoformat(),
                     "predicted_failure_at": predicted.isoformat(), "action": self.action if group == "treated" else None}
            if group == "treated":
                approve = self.post("/api/v1/actions/approve", {"incident_id": incident_id, "action": self.action})
                execute = (self.post("/api/v1/remediation/execute", {"incident_id": incident_id, "action": self.action})
                           if approve.get("status") == "success" else {"status": "skipped", "error": "approval failed"})
                details = execute.get("execution_details") or {}
                entry.update(approve_status=approve.get("status"), execute_status=execute.get("status"),
                             execution_status=details.get("status"), execution_id=details.get("execution_id"),
                             error=execute.get("error") or details.get("error") or approve.get("error"))
            self._append(entry)
            made.append(entry)
            logger.info("Experiment: incident {} -> {}{}", incident_id, group,
                        f" ({entry.get('execution_status') or entry.get('error')})" if group == "treated" else "")
        return made


def summary(log: Path, session_factory: Callable = SessionLocal) -> str:
    entries = read_log(log)
    if not entries:
        return "no experiment entries yet"
    db = session_factory()
    try:
        outcome = {i.id: (i.outcome, i.outcome_detail) for i in
                   db.query(Incident).filter(Incident.id.in_([e["incident_id"] for e in entries])).all()}
    finally:
        db.close()
    lines = []
    for group in ("treated", "control"):
        es = [e for e in entries if e["group"] == group]
        tally: dict[str, int] = {}
        ahead = []
        for e in es:
            o, d = outcome.get(e["incident_id"], (None, None))
            o = o or "awaiting verification"
            tally[o] = tally.get(o, 0) + 1
            d = json.loads(d or "{}")
            if group == "treated" and "minutes_ahead" in d:
                ahead.append(d["minutes_ahead"])
        lines.append(f"{group:8} {len(es):3} incidents: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
        if ahead:
            lines.append(f"         actions ran a median {statistics.median(ahead):.1f} min before the predicted crash")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Controlled experiment: act on every other predicted incident.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--confirm", action="store_true", help="required: you approve these actions in advance")
    run.add_argument("--count", type=int, required=True, help="incidents per group (treated and control)")
    run.add_argument("--namespace", default="remediate-lab")
    run.add_argument("--action", default="RESTART_DEPLOYMENT", choices=["RESTART_DEPLOYMENT", "INCREASE_MEMORY_LIMIT"])
    run.add_argument("--api", default="http://127.0.0.1:8000")
    run.add_argument("--log", type=Path, required=True)
    run.add_argument("--interval", type=float, default=30.0)
    s = sub.add_parser("summary")
    s.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "summary":
        print(summary(args.log))
        return 0
    if not args.confirm:
        print("Refusing to run: add --confirm to approve these actions in advance.")
        return 2
    exp = Experiment(args.namespace, args.count, args.log, http_post(args.api), args.action)
    logger.info("Experiment started: {} treated + {} control in {} ({}); log {}", args.count, args.count,
                args.namespace, args.action, args.log)
    while not exp.done():
        try:
            exp.step()
        except Exception as exc:          # a failed step is logged; the next one tries again
            logger.exception("Experiment step failed: {}", exc)
        time.sleep(args.interval)
    logger.info("Experiment complete: {}", exp.counts())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
