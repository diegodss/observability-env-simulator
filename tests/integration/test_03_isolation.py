"""AT-11 and the optional isolation scenarios."""

import time

from conftest import (
    DEADLINE, INTEGRATIONS, LMS, SIS, compose, is_ready, run_logs, runs, service_states, wait_until,
    worker_metric,
)


def test_at11_one_worker_down_does_not_affect_the_others():
    before = {i: runs(i, "success") for i in ("staff", "courses")}
    compose("stop", "sync-students")
    try:
        assert service_states()["sync-students"]["State"] == "exited"
        for integration in ("staff", "courses"):
            wait_until(lambda: runs(integration, "success") > before[integration], DEADLINE,
                       message=f"{integration} keeps running")
            assert service_states()[f"sync-{integration}"]["State"] == "running"
    finally:
        compose("start", "sync-students")
    wait_until(lambda: worker_metric("students", "integration_last_run_success") == 1, DEADLINE,
               message="students worker back")


def test_sis_outage_is_classified_as_source_failure():
    stopped_at = time.time()
    compose("stop", "sis")
    try:
        assert is_ready(LMS)
        for integration in INTEGRATIONS:
            wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 0, DEADLINE,
                       message=f"{integration} source failure")
            entry = [e for e in run_logs(f"sync-{integration}", stopped_at - 1) if e["outcome"] == "failure"][-1]
            assert entry["error_type"] in ("source_connection_error", "source_timeout")
            assert entry["failed_system"] == "sis"
    finally:
        compose("start", "sis")
    wait_until(lambda: is_ready(SIS), 90, message="SIS readiness")
    for integration in INTEGRATIONS:
        wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 1, DEADLINE,
                   message=f"{integration} recovery after SIS outage")
