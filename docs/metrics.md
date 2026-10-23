# Telemetry reference

The environment only **exposes** telemetry: health endpoints, Prometheus metrics and JSON logs.
It deploys no Prometheus, Alertmanager or Grafana. Those live in the separate
[observability-platform](https://github.com/diegodss/observability-platform) repo, which scrapes
these endpoints over the shared Docker network `observability-sim` and holds the alert rules,
dashboards, Jira Assets model and incident templates.

The services are mocks named after the real systems they stand in for: `technology-one-sm`
(source), `edw` (target), and the three `appevents-*` workers (the DXMULTI-0001 flows).

## Scrape targets

| Service (= Prometheus job in the platform) | Host address | In-network address | Jira Assets CI |
|---|---|---|---|
| `technology-one-sm` | `localhost:8001/metrics` | `technology-one-sm:8000/metrics` | Technology One (SM) |
| `edw` | `localhost:8002/metrics` | `edw:8000/metrics` | EDW. Unreachable while `edw` is stopped. |
| `appevents-student` | `localhost:9101/metrics` | `appevents-student:9100/metrics` | ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student |
| `appevents-general` | `localhost:9102/metrics` | `appevents-general:9100/metrics` | ETL - DXMULTI-0001 : AppEvents from SMS to EDW |
| `appevents-curriculum` | `localhost:9103/metrics` | `appevents-curriculum:9100/metrics` | ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum |

Worker metrics are served from a thread independent of EDW calls, so they stay scrapeable during an
outage.

The platform's Prometheus attaches the Jira Assets CI name to every series as a `ci_name` label;
the services themselves don't need to know it.

## `up` vs `app_up`

- **`up{job=...}`** is synthesised by Prometheus itself: `1` when the scrape succeeds, `0` when the
  target cannot be scraped. A stopped `edw` container shows up **only** here (`up{job="edw"} == 0`),
  because a stopped process can't report anything.
- **`app_up{app=...}`** is emitted by the application.
  - For `technology-one-sm` and `edw` it is evaluated at scrape time: `1` when the process responds
    *and* its database is reachable, `0` when the process responds but the database is not.
  - Workers report `app_up{app="appevents-<name>"} 1` while their scheduler runs.

## Integration worker metrics

Every integration series carries the static labels `integration` (`student`|`curriculum`|`general`),
`source="technology-one-sm"` and `target="edw"`. No run IDs, record IDs, emails or exception text are
used as labels.

| Metric | Type | Extra labels | Meaning |
|---|---|---|---|
| `integration_last_run_success` | Gauge | – | `1` latest completed run succeeded, `0` failed. Absent until the first run completes; never reset between runs. |
| `integration_runs_total` | Counter | `outcome` = `success`\|`failure` | Completed scheduled runs (both outcomes pre-initialised to 0). One run = one schedule tick after retry exhaustion. |
| `integration_run_failures_total` | Counter | `error_type`, `failed_system` = `technology-one-sm`\|`edw`\|`unknown` | Failed runs by classification (bounded set, see below). |
| `integration_last_run_timestamp_seconds` | Gauge | – | Unix time of the most recently completed run. |
| `integration_last_success_timestamp_seconds` | Gauge | – | Unix time of the most recent successful run. |
| `integration_consecutive_failures` | Gauge | – | Failed runs since the last success. |
| `integration_run_duration_seconds` | Histogram | – | Run duration (buckets 0.05 … 60 s). |
| `integration_records_synced_total` | Counter | – | Records successfully sent to the EDW. |
| `app_up` | Gauge | `app` | `1` while the worker scheduler runs. |

Plus the standard `process_*` and `python_*` collectors from `prometheus_client`.

Each flow syncs one mock collection:
- `student` syncs `students`;
- `curriculum` syncs `courses`;
- `general` syncs `staff`. The mock has no organisational-unit data, so staff stands in for the
  general flow's shared reference data.

### Error classification (`error_type`)

| Value | Meaning |
|---|---|
| `target_connection_error` | EDW unreachable: DNS failure (stopped container), refused, reset |
| `target_timeout` | EDW did not answer within `HTTP_TIMEOUT_SECONDS` |
| `target_http_error` | EDW answered 4xx/5xx (other than 422) |
| `source_connection_error` / `source_timeout` / `source_http_error` | same, for Technology One (SM) |
| `validation_error` | EDW rejected the payload (422) or Technology One (SM) returned a malformed body |
| `unexpected_error` | anything else (bug); the scheduler keeps running |

Retries: connection errors, timeouts, 5xx and 429 are retried `HTTP_MAX_RETRIES` times with
`HTTP_RETRY_BACKOFF_SECONDS × attempt` backoff; other 4xx are not retried.

### Example (EDW stopped)

```
integration_last_run_success{integration="general",source="technology-one-sm",target="edw"} 0.0
integration_runs_total{integration="general",outcome="success",source="technology-one-sm",target="edw"} 14.0
integration_runs_total{integration="general",outcome="failure",source="technology-one-sm",target="edw"} 2.0
integration_run_failures_total{error_type="target_connection_error",failed_system="edw",integration="general",source="technology-one-sm",target="edw"} 2.0
integration_consecutive_failures{integration="general",source="technology-one-sm",target="edw"} 2.0
```

## Technology One (SM) and EDW mock metrics

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `app_up` | Gauge | `app` | see above |
| `app_records` | Gauge | `app`, `collection` | rows currently stored |
| `http_requests_total` | Counter | `app`, `method`, `route`, `status_code` | requests handled; `route` is the path template (e.g. `/api/students/{record_id}`), never the raw URL |
| `http_request_duration_seconds` | Histogram | `app`, `method`, `route` | request latency |
| `edw_bulk_upsert_records_total` | Counter | `app`, `collection`, `result` = `inserted`\|`updated`\|`unchanged` | EDW only |

## Structured logs

All services log one JSON object per line to stdout (`docker compose logs <service>`).
Workers emit `event` = `worker_started`, `source_ready`, `attempt_failed` (one per failed HTTP
attempt), `run_completed` (exactly one per run), `worker_stopped`.

`run_completed` fields: `timestamp`, `level` (`info`/`error`), `service`, `event`, `integration`,
`source`, `target`, `dependency` (always `edw`), `run_id`, `outcome`, `duration_ms`, `records`,
`error_type`, `error_message`, `failed_system`, `operation`, `http_status` (null when not applicable),
and on success `upsert` = `{inserted, updated, unchanged}`.

```json
{"timestamp": "2026-10-22T11:02:14.855318+00:00", "level": "error", "service": "appevents-general", "event": "run_completed", "integration": "general", "source": "technology-one-sm", "target": "edw", "dependency": "edw", "run_id": "1ec7a4c5ae844b70", "duration_ms": 1089.2, "outcome": "failure", "records": 0, "error_type": "target_connection_error", "error_message": "ConnectError: [Errno -2] Name or service not known", "failed_system": "edw", "operation": "upsert staff into edw", "http_status": null}
```

## Alert rules

The alert rules built on these metrics are maintained in the observability-platform repo:
[docs/alerting.md](https://github.com/diegodss/observability-platform/blob/main/docs/alerting.md).
