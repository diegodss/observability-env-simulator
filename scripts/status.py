#!/usr/bin/env python3
"""Print a one-screen status of the environment (stdlib only)."""

import json
import os
import re
import urllib.error
import urllib.request

SOURCE = f"http://127.0.0.1:{os.getenv('SOURCE_HOST_PORT', '8001')}"
TARGET = f"http://127.0.0.1:{os.getenv('TARGET_HOST_PORT', '8002')}"
WORKERS = {
    "student": os.getenv("APPEVENTS_STUDENT_METRICS_PORT", "9101"),
    "general": os.getenv("APPEVENTS_GENERAL_METRICS_PORT", "9102"),
    "curriculum": os.getenv("APPEVENTS_CURRICULUM_METRICS_PORT", "9103"),
}


def fetch(url: str):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except (urllib.error.URLError, OSError) as exc:
        return None, str(getattr(exc, "reason", exc))


def metric(text: str, name: str, **labels) -> str:
    for line in text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return line.rsplit(" ", 1)[1]
    return "-"


print("Applications")
for name, base in (("technology-one-sm", SOURCE), ("edw", TARGET)):
    status, _ = fetch(f"{base}/health/ready")
    counts = []
    for collection in ("students", "staff", "courses"):
        code, body = fetch(f"{base}/api/{collection}")
        counts.append(f"{collection}={json.loads(body)['count'] if code == 200 else '-'}")
    state = "READY" if status == 200 else f"DOWN ({status or 'unreachable'})"
    print(f"  {name:<18} {state:<22} {' '.join(counts)}")

print("\nIntegrations")
print(f"  {'name':<11} {'last_ok':<8} {'success':<8} {'failure':<8} {'consec_fail':<12} last_error")
for integration, port in WORKERS.items():
    status, text = fetch(f"http://127.0.0.1:{port}/metrics")
    if status != 200:
        print(f"  {integration:<11} metrics unreachable ({text})")
        continue
    errors = re.findall(r'integration_run_failures_total\{error_type="([^"]+)",failed_system="([^"]+)"', text)
    print(f"  {integration:<11} {metric(text, 'integration_last_run_success'):<8}"
          f" {metric(text, 'integration_runs_total', outcome='success'):<8}"
          f" {metric(text, 'integration_runs_total', outcome='failure'):<8}"
          f" {metric(text, 'integration_consecutive_failures'):<12}"
          f" {', '.join(f'{e}@{s}' for e, s in sorted(set(errors))) or '-'}")
