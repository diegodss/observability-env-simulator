# Education Integration Failure Simulation

A locally runnable, deterministic testbed for an external observability / alert-correlation POC.

- **`sis`** — mock Student Information System (Application A), the source of synthetic students, staff and courses.
- **`lms`** — mock Learning Management System (Application B), receives the replicated records.
- **`sync-students`, `sync-staff`, `sync-courses`** — three independent scheduled integrations (every 30 s).

**The demonstration:** stop `lms` and you get four distinct failure signals with one root cause — the
LMS health check / scrape fails and all three integrations independently report target failures.
Start `lms` again and the jobs recover on their next run without being restarted.

The full build specification is in [docs/requirements/README.md](docs/requirements/README.md).
The Jira Assets model used for alert enrichment, with CSV imports and a lookup test, is in
[docs/jira-assets/README.md](docs/jira-assets/README.md).

> **Not production-hardened.** Synthetic data only, development credentials, a shared static API
> token, ports bound to `127.0.0.1`. Do not expose this to a network.

## Prerequisites

- Docker Desktop (macOS/Windows) or Docker Engine + Compose v2 (Linux). ~1 GB RAM for the stack.
- For the automated tests and helper scripts: `bash`, `curl`, Python 3.10+ on the host
  (Windows: use WSL or Git Bash; the raw `docker compose` commands below work anywhere).
- Free host ports 8001, 8002, 9101–9103 (change them in `.env` if needed).

## Start

```bash
docker compose up --build -d
```

That's the whole setup: databases are migrated and the SIS is seeded on first start, and the
workers wait for the SIS (not the LMS) before their first run. No `.env` is needed; copy
`.env.example` to `.env` only to change defaults. `scripts/up.sh` does the same and then prints status.

```bash
python3 scripts/status.py
```

```
Applications
  sis  READY                  students=24 staff=12 courses=12
  lms  READY                  students=24 staff=12 courses=12

Integrations
  name      last_ok  success  failure  consec_fail  last_error
  students  1.0      3.0      0.0      0.0          -
  ...
```

## Services and ports

| Service | Host URL | In-network | Purpose |
|---|---|---|---|
| `sis` | http://localhost:8001 | `http://sis:8000` | Source API (`/docs` for OpenAPI UI) |
| `lms` | http://localhost:8002 | `http://lms:8000` | Target API (`/docs` for OpenAPI UI) |
| `sync-students` | http://localhost:9101/metrics | `sync-students:9100` | Student Sync worker metrics |
| `sync-staff` | http://localhost:9102/metrics | `sync-staff:9100` | Staff Sync worker metrics |
| `sync-courses` | http://localhost:9103/metrics | `sync-courses:9100` | Course Sync worker metrics |
| `sis-db`, `lms-db` | – (not exposed) | `:5432` | One PostgreSQL 16 per application |
| `prometheus`, `grafana` (optional profile) | http://localhost:9090, http://localhost:3000 | `:9090`, `:3000` | Only with `--profile observability` |

Endpoints on both apps: `/api/{students,staff,courses}`, `/health/live`, `/health/ready`, `/metrics`.
SIS adds `PATCH /api/{collection}/{id}`; LMS adds `PUT /api/{collection}/bulk`.
Full contract: [docs/api.md](docs/api.md). Metrics, logs and suggested alert rules:
[docs/metrics.md](docs/metrics.md).

### Viewing data

```bash
curl -s localhost:8001/api/students            # source
curl -s localhost:8002/api/students            # replicated copy (+ synced_at)
curl -s localhost:9101/metrics | grep ^integration_
docker compose logs -f sync-students           # one JSON line per run
```

## Optional dashboards (Prometheus + Grafana)

Not part of the default stack: `docker compose up --build -d` still starts only the 7 POC services.
Add the monitoring profile when you want a UI:

```bash
docker compose --profile observability up -d
```

| UI | URL | What it shows |
|---|---|---|
| Grafana | http://localhost:3000 | Home dashboard **Education Integration - Jobs and EDW health**. Anonymous view works; log in as `admin` / `admin` to edit. |
| Prometheus | http://localhost:9090/targets | The 5 scrape targets; `lms` turns DOWN during the outage |
| Prometheus alerts | http://localhost:9090/alerts | The suggested rules from [docs/metrics.md](docs/metrics.md), evaluated but not sent anywhere |

The dashboard refreshes every 5 s:

| Section | Panels |
|---|---|
| Health now | UP/DOWN tiles for Technology One (SM) and EDW, and OK/FAILED tiles for each flow |
| Timeline | A status timeline in which the three flows turn red together with EDW |
| Job runs | Runs per minute by outcome; failures by `error_type @ failed_system`; consecutive failures; seconds since the last success; p95 run duration |
| Data | Records synced per minute; records stored per collection in each system |

Prometheus scrapes every 10 s, using the same job names as the Jira Assets **Monitoring Job** key.
It adds a `display_name` label (EDW, AppEvents - Student, …) so panels show real system names.
During an LMS outage it fires `TargetSystemUnavailable` plus three `IntegrationLastRunFailed`
alerts: the four signals that share one root cause.

Stop the monitoring with:

```bash
docker compose --profile observability down
```

The profile is opt-in, and its settings are in `observability/`. The first failure of a new
`error_type` is missing from the "failures by error type" panel, because that counter series only
appears once the first failure happens. The runs-by-outcome panel is pre-initialised and always
shows it.

## Scenario controls

Each scenario has a raw `docker compose` form and a helper script that also waits and prints status.

### 1. Baseline

```bash
docker compose up --build -d          # or: scripts/up.sh
python3 scripts/status.py
```

Expected: `sis` and `lms` READY with equal counts (24/12/12); each integration `last_ok 1.0`.

### 2. Primary outage — stop the LMS

```bash
docker compose stop lms               # or: scripts/simulate-lms-outage.sh (waits 2 intervals)
```

Expected within two intervals (≈ 65 s):

- `lms` unreachable: `curl localhost:8002/health/ready` fails; a Prometheus scrape would give `up{job="lms"}=0`.
- `sis`, `lms-db` and all three workers keep running (`docker compose ps`).
- Each worker: `integration_last_run_success 0`, `integration_runs_total{outcome="failure"}` increasing,
  `integration_run_failures_total{error_type="target_connection_error",failed_system="lms"}` increasing.
- Each worker logs `"outcome": "failure", "target": "lms", "dependency": "lms", "error_type": "target_connection_error"`.
- Worker `/metrics` endpoints keep answering.

`lms` uses `restart: unless-stopped`, so a deliberate `docker compose stop lms` stays down until you start it.

### 3. Recovery

```bash
docker compose start lms              # or: scripts/recover-lms.sh
```

Expected: LMS ready in a few seconds; within one interval all three `last_run_success` gauges return
to `1` and `consecutive_failures` to `0`. Worker containers are not restarted.

### 4. Data update

```bash
scripts/update-record.sh students STU-0001 '{"last_name":"Renamed"}'
# raw form:
curl -X PATCH localhost:8001/api/students/STU-0001 \
  -H 'Authorization: Bearer dev-poc-token' -H 'Content-Type: application/json' \
  -d '{"last_name":"Renamed"}'
sleep 35; curl -s localhost:8002/api/students/STU-0001
```

Expected: the LMS record shows the new value and the same `updated_at` as the SIS after the next
students run; the run log shows `"upsert": {"inserted": 0, "updated": 1, "unchanged": 23}`.

### 5. Isolation (optional)

| Command | Behaviour |
|---|---|
| `docker compose stop sync-students` | staff and courses keep syncing on schedule; students metrics endpoint disappears (scrape `up=0`). `docker compose start sync-students` restores it. |
| `docker compose stop sis` | all three jobs fail with `error_type=source_connection_error`, `failed_system=sis`; LMS stays healthy and keeps its data. Workers keep running and recover after `docker compose start sis`. |
| `docker compose run --rm --no-deps sync-staff --once` | one ad-hoc staff run; exit code 0 on success, 1 on failure (used by tests). |

### 6. Reset (destroys all POC data)

```bash
scripts/reset.sh --yes
# raw form:
docker compose down -v && docker compose up --build -d
```

`down -v` **deletes both database volumes**; the SIS is reseeded on the next start (deterministic,
so IDs and values are identical) and the LMS is repopulated by the first sync runs.

## Configuration

All settings have defaults in `compose.yaml`; override them in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_INTERVAL_SECONDS` | 30 | schedule period for each worker (fixed rate, first run on startup) |
| `HTTP_TIMEOUT_SECONDS` | 5 | per-request timeout |
| `HTTP_MAX_RETRIES` | 1 | retries per request after the first attempt |
| `HTTP_RETRY_BACKOFF_SECONDS` | 1 | backoff × attempt number |
| `SIS_STARTUP_WAIT_SECONDS` | 60 | bounded wait for SIS readiness before the first run |
| `SIS_BASE_URL` / `LMS_BASE_URL` | `http://sis:8000` / `http://lms:8000` | Docker DNS names |
| `API_TOKEN` | `dev-poc-token` | bearer token for write endpoints (empty disables) |
| `*_DB_*` | dev values | database names/users/passwords |
| `SIS_HOST_PORT`, `LMS_HOST_PORT`, `SYNC_*_METRICS_PORT` | 8001, 8002, 9101–9103 | host ports |

Worst-case detection time = 2 × interval + 2 × (2 attempts × timeout + backoff) ≈ 82 s with defaults.

## Tests

```bash
scripts/run-tests.sh                   # unit + integration (stack must be running)
scripts/run-tests.sh tests/unit        # unit only, no Docker needed (<1 s)
scripts/run-tests.sh tests/integration # Docker scenarios only (~5 min at the default 30 s interval)
```

The script creates `.venv` and installs `tests/requirements.txt` on first use. The integration suite
stops/starts `lms`, `sis` and `sync-students`; every test restores what it stopped in a `finally`
block, and a session finalizer runs `docker compose start` on all services, so the environment is
left running even when a test fails. Export `SYNC_INTERVAL_SECONDS` (etc.) if you changed them in
`.env` so the test deadlines match — `scripts/run-tests.sh` does this automatically.

| ID | Covered by |
|---|---|
| AT-01 | `test_01_baseline::test_at01_all_services_operational` |
| AT-02 | `test_01_baseline::test_at02_seed_data_minimums` |
| AT-03 | `test_01_baseline::test_at03_initial_sync_replicates_every_collection` |
| AT-04 | `test_01_baseline::test_at04_repeated_syncs_are_idempotent` |
| AT-05 | `test_02_lms_outage_recovery::test_at05_only_lms_is_down` |
| AT-06 | `test_02_lms_outage_recovery::test_at06_each_integration_fails_independently` |
| AT-07 | `test_02_lms_outage_recovery::test_at07_failed_run_logs_identify_the_target` |
| AT-08 | `test_02_lms_outage_recovery::test_at08_automatic_recovery_without_worker_restarts` |
| AT-09 | `test_01_baseline::test_at09_source_changes_propagate` |
| AT-10 | `test_01_baseline::test_at10_…` and `test_02_lms_outage_recovery::test_at10_metrics_scrapeable_while_lms_down` |
| AT-11 | `test_03_isolation::test_at11_one_worker_down_does_not_affect_the_others` |
| AT-12 | this README + `scripts/run-tests.sh` |
| extra | `test_01_baseline::test_run_once_mode_exit_codes`, `test_03_isolation::test_sis_outage_is_classified_as_source_failure`, `tests/unit/test_sync.py` (classification, retries, log fields) |

## Repository layout

```
compose.yaml, .env.example     one Compose project, defaults inline
app-a-sis/                     SIS: FastAPI app, SQL migrations, deterministic seed
app-b-lms/                     LMS: FastAPI app, SQL migrations, bulk upsert
integrations/                  one worker image, run as three services via INTEGRATION=
tests/unit, tests/integration  pytest suites
scripts/                       up, status, outage, recovery, update, reset, run-tests
docs/                          API contract, metric/log catalogue, suggested rules, requirements
observability/                 optional Prometheus config/rules and Grafana provisioning + dashboard
```

## Design decisions

- **One worker image, three services.** Same code, distinct `INTEGRATION`, job identity, container,
  metrics port and logs. They share nothing at runtime, so one failing cannot affect another.
- **Fresh HTTP client per run**, so no stale keep-alive connection survives an LMS restart.
- **Metrics server in its own thread** (`prometheus_client.start_http_server`), never blocked by sync work.
- **Migrations** are plain SQL files applied at startup and tracked in `schema_migrations`.
- **Upsert skips identical rows** (`IS DISTINCT FROM`), so `synced_at` only moves on real changes and
  the response reports inserted/updated/unchanged.
- **Deletes are not propagated** — out of scope for the spec; records only ever upsert.
- Moodle replacement: implement the LMS contract in [docs/api.md](docs/api.md) (or an adapter in
  front of Moodle web services) and keep the service name `lms`; the workers and telemetry are unchanged.
