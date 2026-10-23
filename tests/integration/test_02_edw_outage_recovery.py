"""AT-05..AT-08, AT-10 (outage): stop only the EDW, observe four failure
conditions with one root cause, then recover without restarting workers.

Tests run in definition order and share the outage; the module fixture always
starts the EDW again, even if an assertion fails part-way."""

import time

import pytest
import requests

from conftest import (
    DEADLINE, INTEGRATIONS, TARGET, SOURCE, WORKER_PORTS, compose, is_ready, run_logs, runs, scrape,
    service_states, started_at, wait_until, worker_metric,
)

WORKERS = [f"appevents-{i}" for i in INTEGRATIONS]
TARGET_ERRORS = {"target_connection_error", "target_timeout"}
state: dict = {}


@pytest.fixture(scope="module", autouse=True)
def edw_outage():
    state["failures_before"] = {i: runs(i, "failure") for i in INTEGRATIONS}
    state["worker_started_at"] = {w: started_at(w) for w in WORKERS}
    state["stopped_at"] = time.time()
    compose("stop", "edw")
    try:
        yield
    finally:
        compose("start", "edw")
        wait_until(lambda: is_ready(TARGET), 90, message="EDW readiness after restore")


def test_at05_only_edw_is_down():
    states = service_states()
    assert states["edw"]["State"] == "exited"
    for service in ("technology-one-sm", "technology-one-sm-db", "edw-db", *WORKERS):
        assert states[service]["State"] == "running", service
    with pytest.raises(requests.ConnectionError):
        requests.get(f"{TARGET}/health/ready", timeout=3)
    assert is_ready(SOURCE)


def test_at06_each_integration_fails_independently():
    for integration in INTEGRATIONS:
        wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 0,
                   DEADLINE, message=f"{integration} last_run_success == 0")
        assert runs(integration, "failure") > state["failures_before"][integration]
        failures_by_target = worker_metric(integration, "integration_run_failures_total",
                                           error_type="target_connection_error", failed_system="edw") or 0
        timeouts = worker_metric(integration, "integration_run_failures_total",
                                 error_type="target_timeout", failed_system="edw") or 0
        assert failures_by_target + timeouts >= 1


def test_at07_failed_run_logs_identify_the_target():
    for integration in INTEGRATIONS:
        failures = [e for e in run_logs(f"appevents-{integration}", state["stopped_at"] - 1) if e["outcome"] == "failure"]
        assert failures, f"no failed run logged for {integration}"
        entry = failures[-1]
        assert entry["integration"] == integration
        assert entry["target"] == "edw" and entry["dependency"] == "edw" and entry["source"] == "technology-one-sm"
        assert entry["error_type"] in TARGET_ERRORS
        assert entry["failed_system"] == "edw"
        assert entry["run_id"] and entry["error_message"]


def test_at10_metrics_scrapeable_while_edw_down():
    for port in WORKER_PORTS.values():
        assert scrape(port)


def test_at08_automatic_recovery_without_worker_restarts():
    compose("start", "edw")
    wait_until(lambda: is_ready(TARGET), 90, message="EDW readiness")
    for integration in INTEGRATIONS:
        wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 1,
                   DEADLINE, message=f"{integration} recovery")
    assert {w: started_at(w) for w in WORKERS} == state["worker_started_at"], "a worker was restarted"
