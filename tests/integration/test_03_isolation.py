"""AT-11 and the optional isolation scenarios."""

import time

from conftest import (
    DEADLINE, INTEGRATIONS, TARGET, SOURCE, compose, is_ready, run_logs, runs, service_states, wait_until,
    worker_metric,
)


def test_at11_one_worker_down_does_not_affect_the_others():
    before = {i: runs(i, "success") for i in ("general", "curriculum")}
    compose("stop", "appevents-student")
    try:
        assert service_states()["appevents-student"]["State"] == "exited"
        for integration in ("general", "curriculum"):
            wait_until(lambda: runs(integration, "success") > before[integration], DEADLINE,
                       message=f"{integration} keeps running")
            assert service_states()[f"appevents-{integration}"]["State"] == "running"
    finally:
        compose("start", "appevents-student")
    wait_until(lambda: worker_metric("student", "integration_last_run_success") == 1, DEADLINE,
               message="student worker back")


def test_source_outage_is_classified_as_source_failure():
    stopped_at = time.time()
    compose("stop", "technology-one-sm")
    try:
        assert is_ready(TARGET)
        for integration in INTEGRATIONS:
            wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 0, DEADLINE,
                       message=f"{integration} source failure")
            entry = [e for e in run_logs(f"appevents-{integration}", stopped_at - 1) if e["outcome"] == "failure"][-1]
            assert entry["error_type"] in ("source_connection_error", "source_timeout")
            assert entry["failed_system"] == "technology-one-sm"
    finally:
        compose("start", "technology-one-sm")
    wait_until(lambda: is_ready(SOURCE), 90, message="Technology One (SM) readiness")
    for integration in INTEGRATIONS:
        wait_until(lambda: worker_metric(integration, "integration_last_run_success") == 1, DEADLINE,
                   message=f"{integration} recovery after Technology One (SM) outage")
