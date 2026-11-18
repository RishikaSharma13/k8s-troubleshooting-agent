"""GET /ui: the dashboard plus a live approval panel, served by the API so its buttons can call it.

The panel lists forecast incidents awaiting approval. A button asks for confirmation, then calls the
same two endpoints a person would call by hand (/actions/approve, then /remediation/execute), so the
approval gate, allowlist, namespace label, server-side dry run and recorded evidence all apply.
The API listens on 127.0.0.1 only, and POSTs from other sites are refused (same_origin_only).
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.models.database import Incident, MemorySample, SessionLocal

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")
MIN_LEAD_S = 180          # do not offer an action when the predicted crash is under 3 minutes away
LIVE_S = 180              # the predicted pod must have reported within this many seconds
LABEL = {"RESTART_DEPLOYMENT": "planned restart", "INCREASE_MEMORY_LIMIT": "raise memory limit (+50%)"}
OTHER = {"RESTART_DEPLOYMENT": "INCREASE_MEMORY_LIMIT", "INCREASE_MEMORY_LIMIT": "RESTART_DEPLOYMENT"}


def pending_incidents(now: Optional[datetime] = None) -> list:
    """Actionable incidents: pending, far enough ahead, the predicted pod still reporting, newest per workload."""
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        rows = (db.query(Incident).filter(Incident.source == "forecast", Incident.status == "PENDING",
                                          Incident.predicted_failure_at > now + timedelta(seconds=MIN_LEAD_S))
                .order_by(Incident.created_at.desc()).all())
        out, seen = [], set()
        for inc in rows:
            key = (inc.namespace, inc.deployment)
            if key in seen:
                continue                          # an older incident for the same workload is superseded
            alive = db.query(MemorySample.id).filter(
                MemorySample.namespace == inc.namespace, MemorySample.pod == inc.pod_name,
                MemorySample.metric_timestamp > now - timedelta(seconds=LIVE_S)).first()
            if alive is None:
                continue                          # the predicted pod is gone (replaced): stale
            seen.add(key)
            out.append(inc)
        return sorted(out, key=lambda i: i.predicted_failure_at)
    finally:
        db.close()


def approval_panel(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    e = html.escape
    rows = []
    for inc in pending_incidents(now):
        t = inc.predicted_failure_at if inc.predicted_failure_at.tzinfo else inc.predicted_failure_at.replace(tzinfo=timezone.utc)
        minutes = (t - now).total_seconds() / 60
        rec = inc.recommended_action if inc.recommended_action in LABEL else "RESTART_DEPLOYMENT"
        alt = OTHER[rec]
        workload = f"{inc.namespace}/{inc.deployment}"
        btn = lambda action, cls: (f'<button class="{cls}" onclick="act(this, {e(repr(inc.id))}, {e(repr(action))}, '
                                   f'{e(repr(workload))})">{"Approve and execute: " if cls == "primary" else ""}'
                                   f'{e(LABEL[action])}{" instead" if cls != "primary" else ""}</button>')
        rows.append(
            f'<tr><td><b>{e(inc.deployment)}</b><br><span class="muted">{e(inc.namespace)}</span></td>'
            f'<td><b>{t.astimezone(IST):%H:%M:%S}</b> IST<br><span class="muted">in {minutes:.0f} min</span></td>'
            f'<td>{e(LABEL[rec])}<br><span class="muted small">{e(inc.reasoning or "")}</span></td>'
            f'<td class="actions">{btn(rec, "primary")}<br>{btn(alt, "secondary")}'
            f'<div class="result" id="res-{e(inc.id)}"></div></td></tr>')
    body = ("<table><tr><th>workload</th><th>predicted crash</th><th>recommended action and reasoning</th><th></th></tr>"
            + "".join(rows) + "</table>") if rows else (
        '<p class="muted">No incidents awaiting approval right now. The watcher raises one when it predicts a crash '
        "within 30 minutes (for the leaking workload, a few minutes after each restart). This panel is read live on "
        "every page load.</p>")
    return ('<div class="card approvals"><h2>Incidents awaiting approval</h2>'
            '<p class="what">What this shows: crashes the system predicts, with its recommended action. Nothing runs '
            "until you approve one exact action; it then runs only after a server-side dry run, in an allow-listed "
            "namespace, and every attempt is recorded (section 7).</p>" + body + "</div>")


STYLE = """<style>
 .approvals button { border-radius: 6px; padding: 7px 12px; margin: 2px 0; cursor: pointer; font-size: 14px }
 .approvals button.primary { background: #17365d; color: #fff; border: 1px solid #17365d; font-weight: 600 }
 .approvals button.secondary { background: #fff; color: #17365d; border: 1px solid #9fb3c8 }
 .approvals button:disabled { opacity: .5; cursor: default }
 .approvals .small { font-size: 12.5px } .approvals .actions { white-space: nowrap; width: 1% }
 .approvals .result { white-space: normal; max-width: 420px; font-size: 13px; margin-top: 6px }
 .approvals .result code { font-size: 12px; word-break: break-all }
</style>"""

SCRIPT = """<script>
let busy = false;
setInterval(() => { if (!busy) location.reload(); }, 60000);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function act(btn, id, action, workload) {
  if (!confirm("Approve and execute " + action + " for " + workload + "?\\n\\nThis changes the cluster. " +
               "It runs only after a server-side dry run, and it is recorded.")) return;
  busy = true;
  const cell = btn.closest("td"), out = document.getElementById("res-" + id);
  cell.querySelectorAll("button").forEach(b => b.disabled = true);
  const q = "incident_id=" + encodeURIComponent(id) + "&action=" + encodeURIComponent(action);
  try {
    out.textContent = "Approving…";
    let r = await fetch("/api/v1/actions/approve?" + q, {method: "POST"}), j = await r.json();
    if (j.status !== "success") throw new Error("not approved: " + (j.error || j.message || j.detail || r.status));
    out.textContent = "Approved. Executing: dry run, the change, then waiting for the rollout (up to 2 min)…";
    r = await fetch("/api/v1/remediation/execute?" + q, {method: "POST"}); j = await r.json();
    const d = j.execution_details || {}, ev = d.evidence || {};
    out.innerHTML = (d.status === "COMPLETED" ? '<b class="ok">Done: executed and rolled out.</b>'
                                              : '<b class="warn">' + esc(d.status || j.status) + ": " + esc(d.error || j.error) + "</b>") +
      (ev.command ? "<br>command: <code>" + esc(ev.command) + "</code>" : "") +
      (ev.dry_run ? "<br>dry run: " + esc(ev.dry_run) : "") +
      (d.old_value ? "<br>before: " + esc(d.old_value) + " → after: " + esc(d.new_value) : "") +
      "<br><span class='muted'>The verdict appears in section 7 about 6 minutes after the predicted crash time.</span>";
  } catch (err) {
    out.innerHTML = '<b class="warn">' + esc(err.message) + "</b>";
    cell.querySelectorAll("button").forEach(b => b.disabled = false);
  }
  busy = false;
  setTimeout(() => { if (!busy) location.reload(); }, 20000);
}
</script>"""


@router.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    path = os.environ.get("DASHBOARD_HTML")
    panel = approval_panel()
    if path and Path(path).exists():
        page = re.sub(r'<meta http-equiv="refresh"[^>]*>', "", Path(path).read_text())   # the script refreshes instead
    else:
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Failure prediction</title>'
                '<style>body { font-family: system-ui, sans-serif; margin: 24px auto; max-width: 1180px }'
                ' .card { border: 1px solid #e3e3e3; border-radius: 8px; padding: 12px 16px; margin: 14px 0 }'
                ' table { border-collapse: collapse; width: 100% } td, th { border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left }'
                ' .muted { color: #888 } .ok { color: #1b7f3b } .warn { color: #b00020 }'
                ' .what { color: #2d5d73; background: #eef6f8; border-left: 4px solid #3b8ea5; padding: 6px 10px }</style>'
                '</head><body><h1>Predicting failures before they happen</h1>'
                '<p class="muted">The full dashboard has not been generated yet (DASHBOARD_HTML is not set or the file '
                'does not exist); the approval panel below is live.</p><div class="card"></div></body></html>')
    m = re.search(r"""<div class=["']card["']>""", page)        # before the first section, whichever quotes it uses
    page = page[:m.start()] + panel + "\n" + page[m.start():] if m else page.replace("</body>", panel + "</body>")
    page = page.replace("live and read-only", "live · actions need your approval", 1)
    return page.replace("</head>", STYLE + "</head>", 1).replace("</body>", SCRIPT + "</body>", 1)
