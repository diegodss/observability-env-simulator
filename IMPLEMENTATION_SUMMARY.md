# Implementation summary

## What was built

- **`sis`** (FastAPI + PostgreSQL `sis-db`): 24 students, 12 staff, 12 courses, seeded the same way every time
  (`STU-0001…`, `STF-0001…`, `CRS-0001…`, `@sis.example` emails). Has GET list/item endpoints and a
  token-protected PATCH for each collection, plus `/health/live`, `/health/ready` and `/metrics`.
- **`lms`** (FastAPI + PostgreSQL `lms-db`): one bulk-upsert endpoint per collection
  (`PUT /api/{collection}/bulk`). Repeating an upsert is safe, and it reports how many rows were
  inserted, updated or left unchanged. Also has GET endpoints to read the data back, the same
  health and metrics endpoints, and a consistent JSON error format with input validation.
- **`sync-students`, `sync-staff`, `sync-courses`**: three containers built from one worker image.
  Each runs every 30 s, with a 5 s timeout and 1 retry. Each has a run-once mode (`--once`). Each
  serves its own `/metrics` (ports 9101–9103) from a separate thread, so metrics stay available
  even when the LMS is down. Each writes one JSON log line per run, with an error type for the
  system that failed: `target_*` for LMS problems, `source_*` for SIS problems.
- Scenario scripts in `scripts/`, the API contract in `docs/api.md`, the metric and log catalogue
  plus suggested alert rules in `docs/metrics.md`, and the README.

## Commands used to test it (2026-10-22, Docker Desktop 29.7.2 on macOS, Python 3.14 host venv)

```bash
scripts/reset.sh --yes        # docker compose down -v; docker compose up --build -d; status
scripts/run-tests.sh -v       # pytest tests/unit tests/integration
```

Result: **22 passed in 293 s** (8 unit, 14 integration, covering AT-01 to AT-11). An earlier run of the
integration suite on its own also passed (14 passed in 299 s). These scripts were also run by hand,
and their output matched the README:
`simulate-lms-outage.sh` (all three jobs showed `last_ok 0` with `target_connection_error@lms`),
`recover-lms.sh` (all three were back to `1` within one interval) and `update-record.sh`
(the change reached the LMS in about 13 s).

Memory in steady state: about 240 MiB in total across the 7 containers.

## Limitations and decisions

- Bash helper scripts. On Windows, use WSL or Git Bash, or the raw `docker compose` commands in the README.
- Deletes are not replicated. Records are only ever upserted.
- When the LMS is stopped, Docker DNS can no longer resolve `lms`, so that error shows up as
  `target_connection_error` (`Name or service not known`). `target_timeout` only appears if the
  LMS is reachable but slow.
- `integration_runs_total` and the other worker metrics also carry `source` and `target` labels,
  in addition to the labels the spec requires, so every integration signal names its dependency.
  Two extra metrics were added: `integration_run_failures_total{error_type,failed_system}` and
  `integration_consecutive_failures`.
- Worker counters reset to 0 when a worker container restarts. Per the spec, run history is only
  kept as counters and logs.
- This setup is not hardened for production: it uses a shared static token, development database
  passwords, and ports bound to localhost only.

## Later changes (after this summary)

This summary records the first build and its test run. The environment has changed since:

- **Renamed after the real systems** the mocks stand in for:

  | Then | Now |
  |---|---|
  | `sis` | `technology-one-sm` |
  | `lms` | `edw` |
  | `sync-students` | `appevents-student` |
  | `sync-staff` | `appevents-general` |
  | `sync-courses` | `appevents-curriculum` |

  - Folders, environment variables, metric label values (`integration=student|curriculum|general`,
    `source=technology-one-sm`, `target=edw`), scripts and tests were renamed to match.
  - `lms_bulk_upsert_records_total` is now `edw_bulk_upsert_records_total`.
- **Prometheus + Grafana, alert rules, the Jira Assets model and the incident ticket templates** were
  added here for a while, then moved to their own repo,
  [observability-platform](https://github.com/diegodss/observability-platform). This repo now only
  simulates the apps; it exposes the shared Docker network `observability-sim` for the platform.

See the README for current commands.

