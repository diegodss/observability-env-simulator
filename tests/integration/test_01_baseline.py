"""AT-01..AT-04, AT-09, AT-10 (baseline): healthy stack behaviour."""

import time

import requests

from conftest import (
    ALL_SERVICES, DEADLINE, ID_FIELDS, INTEGRATIONS, INTERVAL, LMS, RUN_BUDGET, SIS, TOKEN, WORKER_PORTS,
    compose, records, runs, scrape, service_states, wait_until,
)


def test_at01_all_services_operational():
    states = service_states()
    for service in ALL_SERVICES:
        assert states[service]["State"] == "running", f"{service}: {states[service]['Status']}"
    for service in ("sis", "lms", "sis-db", "lms-db"):
        assert states[service]["Health"] == "healthy", service
    for base in (SIS, LMS):
        assert requests.get(f"{base}/health/live", timeout=5).status_code == 200
        assert requests.get(f"{base}/health/ready", timeout=5).status_code == 200


def test_at02_seed_data_minimums():
    assert len(records(SIS, "students")) >= 20
    assert len(records(SIS, "staff")) >= 10
    assert len(records(SIS, "courses")) >= 10


def test_at03_initial_sync_replicates_every_collection():
    for collection in INTEGRATIONS:
        id_field = ID_FIELDS[collection]
        source_ids = {r[id_field] for r in records(SIS, collection)}
        wait_until(lambda: {r[id_field] for r in records(LMS, collection)} == source_ids,
                   DEADLINE, message=f"LMS {collection} to match SIS")


def test_at04_repeated_syncs_are_idempotent():
    before_counts = {c: len(records(LMS, c)) for c in INTEGRATIONS}
    before_runs = {c: runs(c, "success") for c in INTEGRATIONS}
    for collection in INTEGRATIONS:
        wait_until(lambda: runs(collection, "success") >= before_runs[collection] + 2,
                   2 * INTERVAL + RUN_BUDGET + 10, message=f"two more {collection} runs")
    for collection in INTEGRATIONS:
        items = records(LMS, collection)
        ids = [r[ID_FIELDS[collection]] for r in items]
        assert len(items) == before_counts[collection]
        assert len(ids) == len(set(ids)), f"duplicate ids in LMS {collection}"


def test_at09_source_changes_propagate():
    marker = f"Updated {int(time.time())}"
    changes = {
        "students": ("STU-0003", {"last_name": f"Sample {marker}"}),
        "staff": ("STF-0002", {"department": f"Mathematics {marker}"}),
        "courses": ("CRS-0004", {"title": f"Data Structures Placeholder {marker}"}),
    }
    for collection, (record_id, patch) in changes.items():
        response = requests.patch(f"{SIS}/api/{collection}/{record_id}", json=patch,
                                  headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
        assert response.status_code == 200, response.text
        updated_at = response.json()["updated_at"]
        field, value = next(iter(patch.items()))

        def propagated():
            target = requests.get(f"{LMS}/api/{collection}/{record_id}", timeout=5).json()
            return target[field] == value and target["updated_at"] == updated_at

        wait_until(propagated, INTERVAL + RUN_BUDGET + 10, message=f"{collection}/{record_id} in LMS")


def test_at10_worker_metrics_are_valid_prometheus_text():
    for integration, port in WORKER_PORTS.items():
        samples = scrape(port)
        names = {name for name, _ in samples}
        for required in ("integration_last_run_success", "integration_runs_total",
                         "integration_last_run_timestamp_seconds", "integration_run_duration_seconds_count",
                         "integration_records_synced_total", "app_up"):
            assert required in names, f"{integration} missing {required}"


def test_run_once_mode_exit_codes():
    ok = compose("run", "--rm", "--no-deps", "sync-staff", "--once", check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    failed = compose("run", "--rm", "--no-deps", "-e", "LMS_BASE_URL=http://lms-missing:8000",
                     "-e", "HTTP_TIMEOUT_SECONDS=2", "sync-staff", "--once", check=False)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert '"error_type": "target_connection_error"' in failed.stdout
