# Telemetry reference

The environment only **exposes** telemetry: health endpoints, Prometheus metrics and JSON logs.
By default no Prometheus, Alertmanager or Grafana is deployed. For local viewing, an opt-in
`observability` Compose profile adds Prometheus (these scrape jobs and the rules below) and Grafana
with a provisioned dashboard. See the README.

## Scrape targets

| Prometheus job (suggested) | Host address | In-network address | Notes |
|---|---|---|---|
| `sis` | `localhost:8001/metrics` | `sis:8000/metrics` | |
| `lms` | `localhost:8002/metrics` | `lms:8000/metrics` | unreachable while `lms` is stopped |
| `sync-students` | `localhost:9101/metrics` | `sync-students:9100/metrics` | served from a thread independent of LMS calls |
| `sync-staff` | `localhost:9102/metrics` | `sync-staff:9100/metrics` | |
| `sync-courses` | `localhost:9103/metrics` | `sync-courses:9100/metrics` | |

## `up` vs `app_up`

- **`up{job=...}`** is synthesised by Prometheus itself: `1` when the scrape succeeds, `0` when the
  target cannot be scraped. A stopped `lms` container shows up **only** here (`up{job="lms"} == 0`),
  because a stopped process can't report anything.
- **`app_up{app=...}`** is emitted by the application. For SIS/LMS it is evaluated at scrape time:
  `1` when the process responds *and* its database is reachable, `0` when the process responds but
  the database is not. Workers report `app_up{app="sync-<name>"} 1` while their scheduler runs.

## Integration worker metrics

Every integration series carries the static labels `integration` (`students`|`staff`|`courses`),
`source="sis"` and `target="lms"`. No run IDs, record IDs, emails or exception text are used as labels.

| Metric | Type | Extra labels | Meaning |
|---|---|---|---|
| `integration_last_run_success` | Gauge | – | `1` latest completed run succeeded, `0` failed. Absent until the first run completes; never reset between runs. |
| `integration_runs_total` | Counter | `outcome` = `success`\|`failure` | Completed scheduled runs (both outcomes pre-initialised to 0). One run = one schedule tick after retry exhaustion. |
| `integration_run_failures_total` | Counter | `error_type`, `failed_system` = `sis`\|`lms`\|`unknown` | Failed runs by classification (bounded set, see below). |
| `integration_last_run_timestamp_seconds` | Gauge | – | Unix time of the most recently completed run. |
| `integration_last_success_timestamp_seconds` | Gauge | – | Unix time of the most recent successful run. |
| `integration_consecutive_failures` | Gauge | – | Failed runs since the last success. |
| `integration_run_duration_seconds` | Histogram | – | Run duration (buckets 0.05 … 60 s). |
| `integration_records_synced_total` | Counter | – | Records successfully sent to the LMS. |
| `app_up` | Gauge | `app` | `1` while the worker scheduler runs. |

Plus the standard `process_*` and `python_*` collectors from `prometheus_client`.

### Error classification (`error_type`)

| Value | Meaning |
|---|---|
| `target_connection_error` | LMS unreachable: DNS failure (stopped container), refused, reset |
| `target_timeout` | LMS did not answer within `HTTP_TIMEOUT_SECONDS` |
| `target_http_error` | LMS answered 4xx/5xx (other than 422) |
| `source_connection_error` / `source_timeout` / `source_http_error` | same, for the SIS |
| `validation_error` | LMS rejected the payload (422) or the SIS returned a malformed body |
| `unexpected_error` | anything else (bug); the scheduler keeps running |

Retries: connection errors, timeouts, 5xx and 429 are retried `HTTP_MAX_RETRIES` times with
`HTTP_RETRY_BACKOFF_SECONDS × attempt` backoff; other 4xx are not retried.

### Example (LMS stopped)

```
integration_last_run_success{integration="staff",source="sis",target="lms"} 0.0
integration_runs_total{integration="staff",outcome="success",source="sis",target="lms"} 14.0
integration_runs_total{integration="staff",outcome="failure",source="sis",target="lms"} 2.0
integration_run_failures_total{error_type="target_connection_error",failed_system="lms",integration="staff",source="sis",target="lms"} 2.0
integration_consecutive_failures{integration="staff",source="sis",target="lms"} 2.0
```

## SIS / LMS application metrics

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `app_up` | Gauge | `app` | see above |
| `app_records` | Gauge | `app`, `collection` | rows currently stored |
| `http_requests_total` | Counter | `app`, `method`, `route`, `status_code` | requests handled; `route` is the path template (e.g. `/api/students/{record_id}`), never the raw URL |
| `http_request_duration_seconds` | Histogram | `app`, `method`, `route` | request latency |
| `lms_bulk_upsert_records_total` | Counter | `app`, `collection`, `result` = `inserted`\|`updated`\|`unchanged` | LMS only |

## Structured logs

All services log one JSON object per line to stdout (`docker compose logs <service>`).
Workers emit `event` = `worker_started`, `source_ready`, `attempt_failed` (one per failed HTTP
attempt), `run_completed` (exactly one per run), `worker_stopped`.

`run_completed` fields: `timestamp`, `level` (`info`/`error`), `service`, `event`, `integration`,
`source`, `target`, `dependency` (always `lms`), `run_id`, `outcome`, `duration_ms`, `records`,
`error_type`, `error_message`, `failed_system`, `operation`, `http_status` (null when not applicable),
and on success `upsert` = `{inserted, updated, unchanged}`.

```json
{"timestamp": "2026-10-22T11:02:14.855318+00:00", "level": "error", "service": "sync-staff", "event": "run_completed", "integration": "staff", "source": "sis", "target": "lms", "dependency": "lms", "run_id": "1ec7a4c5ae844b70", "duration_ms": 1089.2, "outcome": "failure", "records": 0, "error_type": "target_connection_error", "error_message": "ConnectError: [Errno -2] Name or service not known", "failed_system": "lms", "operation": "upsert staff into lms", "http_status": null}
```

## Suggested future rules (not installed)

```yaml
groups:
  - name: education-integration-poc
    rules:
      - alert: LmsUnavailable            # Prometheus scrape failure, not app_up
        expr: up{job="lms"} == 0
        for: 30s
        labels: {service: lms}
      - alert: LmsUnhealthy              # process up but its database is not
        expr: app_up{app="lms"} == 0
        for: 30s
      - alert: IntegrationLastRunFailed  # only after at least one completed run
        expr: integration_last_run_success == 0 and on(integration) integration_runs_total{outcome="failure"} > 0
        labels: {dependency: lms}
      - alert: IntegrationStale
        expr: time() - integration_last_success_timestamp_seconds > 3 * 30
```

A correlator can group `IntegrationLastRunFailed` for all three integrations with
`LmsUnavailable` via the shared `target="lms"` label and overlapping time window.
