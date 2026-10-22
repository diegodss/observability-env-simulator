"""Helpers for tests that drive the running Docker Compose stack from the host.

Every test that stops a container restores it in a finalizer, and a
session-level finalizer starts every service again even if a test failed.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[2]
SIS = f"http://127.0.0.1:{os.getenv('SIS_HOST_PORT', '8001')}"
LMS = f"http://127.0.0.1:{os.getenv('LMS_HOST_PORT', '8002')}"
WORKER_PORTS = {
    "students": int(os.getenv("SYNC_STUDENTS_METRICS_PORT", "9101")),
    "staff": int(os.getenv("SYNC_STAFF_METRICS_PORT", "9102")),
    "courses": int(os.getenv("SYNC_COURSES_METRICS_PORT", "9103")),
}
ID_FIELDS = {"students": "student_id", "staff": "staff_id", "courses": "course_id"}
INTEGRATIONS = list(WORKER_PORTS)
TOKEN = os.getenv("API_TOKEN", "dev-poc-token")

INTERVAL = float(os.getenv("SYNC_INTERVAL_SECONDS", "30"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5"))
RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "1"))
BACKOFF = float(os.getenv("HTTP_RETRY_BACKOFF_SECONDS", "1"))
# Spec: status changes observable within two intervals plus the timeout/retry budget.
RUN_BUDGET = 2 * ((RETRIES + 1) * HTTP_TIMEOUT + RETRIES * BACKOFF)
DEADLINE = 2 * INTERVAL + RUN_BUDGET + 10

ALL_SERVICES = ["sis-db", "lms-db", "sis", "lms", "sync-students", "sync-staff", "sync-courses"]


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def wait_until(predicate, timeout: float, interval: float = 1.0, message: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception as exc:  # retried until the deadline
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {message}; last result: {last!r}")


def scrape(port: int) -> dict[tuple, float]:
    """Parse a worker /metrics page (validating the Prometheus text format)."""
    response = requests.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
    response.raise_for_status()
    samples = {}
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            samples[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return samples


def worker_metric(integration: str, name: str, **extra) -> float | None:
    labels = {"integration": integration, "source": "sis", "target": "lms", **extra}
    return scrape(WORKER_PORTS[integration]).get((name, tuple(sorted(labels.items()))))


def runs(integration: str, outcome: str) -> float:
    return worker_metric(integration, "integration_runs_total", outcome=outcome) or 0.0


def records(base: str, collection: str) -> list[dict]:
    response = requests.get(f"{base}/api/{collection}", timeout=5)
    response.raise_for_status()
    return response.json()["items"]


def is_ready(base: str) -> bool:
    try:
        return requests.get(f"{base}/health/ready", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def service_states() -> dict[str, dict]:
    out = compose("ps", "--all", "--format", "json").stdout.strip()
    rows = json.loads(out) if out.startswith("[") else [json.loads(line) for line in out.splitlines() if line]
    return {row["Service"]: row for row in rows}


def started_at(service: str) -> str:
    container = compose("ps", "-q", service).stdout.strip()
    return subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
                          check=True, capture_output=True, text=True).stdout.strip()


def run_logs(service: str, since: float) -> list[dict]:
    out = compose("logs", "--no-log-prefix", "--since", str(int(since)), service).stdout
    entries = []
    for line in out.splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [e for e in entries if e.get("event") == "run_completed"]


def wait_for_stack(timeout: float = DEADLINE + 60) -> None:
    wait_until(lambda: is_ready(SIS) and is_ready(LMS), timeout, message="SIS and LMS readiness")
    for integration in INTEGRATIONS:
        wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 1, timeout,
                   message=f"a successful {integration} run")


def restore_all() -> None:
    compose("start", *ALL_SERVICES, check=False)


@pytest.fixture(scope="session", autouse=True)
def stack():
    states = service_states()
    missing = [s for s in ALL_SERVICES if s not in states]
    if missing:
        pytest.exit(f"Stack is not running (missing {missing}); run `docker compose up --build -d` first", 2)
    restore_all()
    wait_for_stack()
    yield
    restore_all()
