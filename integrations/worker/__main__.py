"""Scheduled integration worker.

    python -m worker          run every SYNC_INTERVAL_SECONDS, serving /metrics
    python -m worker --once   run a single sync and exit 0 (success) or 1 (failure)
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from .sync import SOURCE, TARGET, Config, SyncError, sync_once

LABELS = ["integration", "source", "target"]

APP_UP = Gauge("app_up", "1 while the worker process is running its scheduler", ["app"])
LAST_RUN_SUCCESS = Gauge("integration_last_run_success", "1 if the latest completed run succeeded, 0 if it failed", LABELS)
RUNS = Counter("integration_runs", "Completed scheduled runs by outcome", [*LABELS, "outcome"])
FAILURES = Counter("integration_run_failures", "Failed runs by error classification", [*LABELS, "error_type", "failed_system"])
LAST_RUN_TS = Gauge("integration_last_run_timestamp_seconds", "Unix time of the most recently completed run", LABELS)
LAST_SUCCESS_TS = Gauge("integration_last_success_timestamp_seconds", "Unix time of the most recent successful run", LABELS)
CONSECUTIVE_FAILURES = Gauge("integration_consecutive_failures", "Failed runs since the last success", LABELS)
DURATION = Histogram("integration_run_duration_seconds", "Duration of completed runs", LABELS,
                     buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30, 60))
RECORDS = Counter("integration_records_synced", "Records successfully sent to the target", LABELS)


def emit(config: Config, level: str, event: str, **fields) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "service": config.service,
        "event": event,
        "integration": config.integration,
        "source": SOURCE,
        "target": TARGET,
        "dependency": TARGET,
        **fields,
    }
    print(json.dumps(entry), flush=True)


def execute_run(config: Config) -> bool:
    labels = (config.integration, SOURCE, TARGET)
    run_id = uuid.uuid4().hex[:16]
    started = time.perf_counter()

    def attempt_failed(error: SyncError, attempt: int, will_retry: bool) -> None:
        emit(config, "warning", "attempt_failed", run_id=run_id, attempt=attempt, will_retry=will_retry,
             operation=error.operation, failed_system=error.system, error_type=error.error_type,
             http_status=error.http_status, error_message=error.message)

    result = {"outcome": "success", "records": 0, "error_type": None, "error_message": None,
              "failed_system": None, "operation": None, "http_status": None}
    try:
        # A fresh client per run: no stale keep-alive connections survive an LMS restart.
        with httpx.Client(timeout=config.timeout_seconds) as client:
            summary = sync_once(client, config, on_attempt_failed=attempt_failed)
        result["records"] = summary["records"]
        result["upsert"] = {k: summary.get(k) for k in ("inserted", "updated", "unchanged")}
    except SyncError as error:
        result.update(outcome="failure", error_type=error.error_type, error_message=error.message,
                      failed_system=error.system, operation=error.operation, http_status=error.http_status)
    except Exception as error:  # never let one bad run stop the scheduler
        result.update(outcome="failure", error_type="unexpected_error",
                      error_message=f"{type(error).__name__}: {error}")

    duration = time.perf_counter() - started
    success = result["outcome"] == "success"
    now = time.time()
    RUNS.labels(*labels, result["outcome"]).inc()
    DURATION.labels(*labels).observe(duration)
    LAST_RUN_TS.labels(*labels).set(now)
    LAST_RUN_SUCCESS.labels(*labels).set(1 if success else 0)
    if success:
        RECORDS.labels(*labels).inc(result["records"])
        LAST_SUCCESS_TS.labels(*labels).set(now)
        CONSECUTIVE_FAILURES.labels(*labels).set(0)
    else:
        FAILURES.labels(*labels, result["error_type"], result["failed_system"] or "unknown").inc()
        CONSECUTIVE_FAILURES.labels(*labels).inc()

    emit(config, "info" if success else "error", "run_completed", run_id=run_id,
         duration_ms=round(duration * 1000, 1), **result)
    return success


def wait_for_sis(config: Config, stop: threading.Event) -> None:
    """Bounded wait for SIS readiness; the LMS is deliberately not awaited."""
    deadline = time.monotonic() + config.sis_startup_wait_seconds
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            if httpx.get(f"{config.sis_base_url}/health/ready", timeout=config.timeout_seconds).status_code == 200:
                emit(config, "info", "source_ready")
                return
        except httpx.HTTPError:
            pass
        stop.wait(2)
    if not stop.is_set():
        emit(config, "warning", "source_not_ready", error_message="SIS not ready; starting schedule anyway")


def run_forever(config: Config) -> None:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    labels = (config.integration, SOURCE, TARGET)
    for outcome in ("success", "failure"):
        RUNS.labels(*labels, outcome)
    RECORDS.labels(*labels)
    CONSECUTIVE_FAILURES.labels(*labels).set(0)

    start_http_server(config.metrics_port)
    APP_UP.labels(config.service).set(1)
    emit(config, "info", "worker_started", interval_seconds=config.interval_seconds,
         timeout_seconds=config.timeout_seconds, max_retries=config.max_retries,
         metrics_port=config.metrics_port)

    wait_for_sis(config, stop)
    next_run = time.monotonic()
    while not stop.wait(max(0.0, next_run - time.monotonic())):
        execute_run(config)
        next_run = max(next_run + config.interval_seconds, time.monotonic())

    APP_UP.labels(config.service).set(0)
    emit(config, "info", "worker_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(prog="worker")
    parser.add_argument("--once", action="store_true", help="run a single sync and exit")
    args = parser.parse_args()
    config = Config.from_env()
    if args.once or os.getenv("RUN_ONCE", "").lower() in ("1", "true", "yes"):
        sys.exit(0 if execute_run(config) else 1)
    run_forever(config)


if __name__ == "__main__":
    main()
