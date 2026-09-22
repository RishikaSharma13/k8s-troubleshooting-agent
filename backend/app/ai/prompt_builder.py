import json
from typing import Any

SYSTEM_PROMPT = """You are a senior Kubernetes SRE. Analyze only the supplied Kubernetes evidence.
Correlate pod status, logs, events, deployment health, and networking findings.
Do not invent facts. If evidence is insufficient, say so and lower confidence.
Return valid JSON only with exactly these keys:
root_cause, explanation, suggested_fix, kubectl_commands, prevention_recommendation, confidence.
confidence must be an integer from 0 to 100. kubectl_commands must be an array of safe command strings.
"""


def build_messages(investigation: dict[str, Any]) -> list[dict[str, str]]:
    evidence = json.dumps(investigation, indent=2, ensure_ascii=False)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze this Kubernetes investigation evidence:\n\n{evidence}"},
    ]
