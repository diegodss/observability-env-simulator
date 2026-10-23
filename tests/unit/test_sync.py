"""Unit tests for the worker's sync and failure classification (no Docker needed)."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations"))

from worker import __main__ as runner  # noqa: E402
from worker.sync import Config, SyncError, sync_once  # noqa: E402

CONFIG = Config(integration="general", api_token="t", max_retries=1, retry_backoff_seconds=0)
SOURCE_ITEMS = [{"staff_id": "STF-0001", "first_name": "A", "last_name": "B", "email": "a@b.example",
              "department": "X", "status": "active", "updated_at": "2026-01-01T00:00:00+00:00"}]


def client_for(handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


def source_ok_then(target_handler):
    def handler(request):
        if request.url.host == "technology-one-sm":
            return httpx.Response(200, json={"items": SOURCE_ITEMS, "count": 1})
        return target_handler(request)
    return handler


def test_success_reads_only_own_collection_and_sends_token():
    client, calls = client_for(source_ok_then(
        lambda r: httpx.Response(200, json={"inserted": 1, "updated": 0, "unchanged": 0})))
    summary = sync_once(client, CONFIG)
    assert summary["records"] == 1
    assert [str(c.url) for c in calls] == ["http://technology-one-sm:8000/api/staff", "http://edw:8000/api/staff/bulk"]
    assert calls[1].headers["authorization"] == "Bearer t"
    assert json.loads(calls[1].content) == {"records": SOURCE_ITEMS}


def test_edw_unreachable_is_target_connection_error_after_one_retry():
    def edw_down(request):
        raise httpx.ConnectError("Name or service not known", request=request)

    client, calls = client_for(source_ok_then(edw_down))
    attempts = []
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG, on_attempt_failed=lambda e, a, retry: attempts.append((a, retry)))
    assert err.value.error_type == "target_connection_error"
    assert err.value.system == "edw"
    assert attempts == [(1, True), (2, False)]
    assert len(calls) == 3  # one source read, two target attempts


def test_edw_timeout_is_target_timeout():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = client_for(source_ok_then(slow))
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG)
    assert err.value.error_type == "target_timeout"


def test_edw_5xx_is_retried_target_http_error():
    client, calls = client_for(source_ok_then(
        lambda r: httpx.Response(503, json={"error": {"code": "database_unavailable", "message": "down"}})))
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG)
    assert (err.value.error_type, err.value.http_status) == ("target_http_error", 503)
    assert len(calls) == 3


def test_edw_422_is_validation_error_and_not_retried():
    client, calls = client_for(source_ok_then(
        lambda r: httpx.Response(422, json={"error": {"code": "validation_error", "message": "bad"}})))
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG)
    assert err.value.error_type == "validation_error"
    assert len(calls) == 2


def test_source_unreachable_is_source_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = client_for(handler)
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG)
    assert (err.value.error_type, err.value.system) == ("source_connection_error", "technology-one-sm")


def test_malformed_source_payload_is_validation_error():
    client, _ = client_for(lambda r: httpx.Response(200, json={"items": [{"no_id": 1}]}))
    with pytest.raises(SyncError) as err:
        sync_once(client, CONFIG)
    assert (err.value.error_type, err.value.system) == ("validation_error", "technology-one-sm")


def test_failed_run_updates_metrics_and_logs_target_identity(monkeypatch, capsys):
    def fail(*_, **__):
        raise SyncError("target_connection_error", "boom", system="edw", operation="upsert staff into edw")

    monkeypatch.setattr(runner, "sync_once", fail)
    assert runner.execute_run(CONFIG) is False
    labels = ("general", "technology-one-sm", "edw")
    assert runner.LAST_RUN_SUCCESS.labels(*labels)._value.get() == 0
    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert entry["event"] == "run_completed"
    assert entry["outcome"] == "failure"
    assert (entry["target"], entry["dependency"], entry["integration"]) == ("edw", "edw", "general")
    assert entry["error_type"] == "target_connection_error"
    for key in ("timestamp", "level", "service", "source", "run_id", "duration_ms", "records", "error_message"):
        assert key in entry

    monkeypatch.setattr(runner, "sync_once", lambda *_, **__: {"records": 1})
    assert runner.execute_run(CONFIG) is True
    assert runner.LAST_RUN_SUCCESS.labels(*labels)._value.get() == 1
    assert runner.CONSECUTIVE_FAILURES.labels(*labels)._value.get() == 0
