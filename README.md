# Education Integration Failure Simulation

A locally runnable, deterministic testbed for an external observability / alert-correlation POC.
It simulates two real systems and the integrations between them, using mock services named after
the systems they stand in for:

| Service | Stands in for | Jira Assets CI (`ci_name`) |
|---|---|---|
| **`technology-one-sm`** | Technology One (SM), the student management system (source) | Technology One (SM) |
| **`edw`** | EDW, the enterprise data warehouse (target) | EDW |
| **`appevents-student`** | Student AppEvents flow | ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student |
| **`appevents-curriculum`** | Curriculum AppEvents flow | ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum |
| **`appevents-general`** | General AppEvents flow (shared reference data) | ETL - DXMULTI-0001 : AppEvents from SMS to EDW |

The mocks hold synthetic data:
- students, staff and courses, in `technology-one-sm`;
- the replicated copy, in `edw`.

Each worker syncs one collection: student → students, curriculum → courses, general → staff. The mock
has no organisational-unit data, so the staff list stands in for the general flow. The fourth real
flow, Study Plan, exists in Jira Assets but isn't simulated. The workers run every 30 s; the real
flows run every 5 minutes.

**The demonstration:** stop `edw` and you get four distinct failure signals with one root cause:
- the EDW health check / scrape fails;
- all three AppEvents workers independently report target failures.

Start `edw` again and the workers recover on their next run without being restarted.

**Further reading:**
- [docs/jira-assets/README.md](docs/jira-assets/README.md): the Jira Assets model used for alert enrichment, with CSV imports and a lookup test.
- [docs/incident-templates.md](docs/incident-templates.md): the incident ticket templates.
- [docs/requirements/README.md](docs/requirements/README.md): the original build specification. It uses the earlier mock names SIS/LMS.

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

That's the whole setup:
- databases are migrated and `technology-one-sm` is seeded on first start;
- the workers wait for `technology-one-sm` (not `edw`) before their first run;
- no `.env` is needed. Copy `.env.example` to `.env` only to change defaults.

`scripts/up.sh` does the same and then prints the status.

```bash
python3 scripts/status.py
```

```
Applications
  technology-one-sm  READY                  students=24 staff=12 courses=12
  edw                READY                  students=24 staff=12 courses=12

Integrations
  name        last_ok  success  failure  consec_fail  last_error
  student     1.0      3.0      0.0      0.0          -
  ...
```

## Services and ports

| Service | Host URL | In-network | Purpose |
|---|---|---|---|
| `technology-one-sm` | http://localhost:8001 | `http://technology-one-sm:8000` | Source API (`/docs` for OpenAPI UI) |
| `edw` | http://localhost:8002 | `http://edw:8000` | Target API (`/docs` for OpenAPI UI) |
| `appevents-student` | http://localhost:9101/metrics | `appevents-student:9100` | Student flow worker metrics |
| `appevents-general` | http://localhost:9102/metrics | `appevents-general:9100` | General flow worker metrics |
| `appevents-curriculum` | http://localhost:9103/metrics | `appevents-curriculum:9100` | Curriculum flow worker metrics |
| `technology-one-sm-db`, `edw-db` | – (not exposed) | `:5432` | One PostgreSQL 16 per application |
| `prometheus`, `grafana` (optional profile) | http://localhost:9090, http://localhost:3000 | `:9090`, `:3000` | Only with `--profile observability` |

Endpoints:
- **Both apps:** `/api/{students,staff,courses}`, `/health/live`, `/health/ready`, `/metrics`.
- **`technology-one-sm` adds:** `PATCH /api/{collection}/{id}`.
- **`edw` adds:** `PUT /api/{collection}/bulk`.

Full contract: [docs/api.md](docs/api.md). Metrics, logs and alert rules:
[docs/metrics.md](docs/metrics.md).

### Viewing data

```bash
curl -s localhost:8001/api/students            # source (Technology One (SM) mock)
curl -s localhost:8002/api/students            # replicated copy in the EDW mock (+ synced_at)
curl -s localhost:9101/metrics | grep ^integration_
docker compose logs -f appevents-student       # one JSON line per run
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
| Prometheus | http://localhost:9090/targets | The 5 scrape targets; `edw` turns DOWN during the outage |
| Prometheus alerts | http://localhost:9090/alerts | The alert rules from [docs/metrics.md](docs/metrics.md), evaluated but not sent anywhere |

The dashboard refreshes every 5 s:

| Section | Panels |
|---|---|
| Health now | UP/DOWN tiles for Technology One (SM) and EDW, and OK/FAILED tiles for each flow |
| Timeline | A status timeline in which the three flows turn red together with EDW |
| Job runs | Runs per minute by outcome; failures by `error_type @ failed_system`; consecutive failures; seconds since the last success; p95 run duration |
| Data | Records synced per minute; records stored per collection in each system |

Prometheus scrapes every 10 s and adds two labels:
- `ci_name`: the exact Jira Assets object name of each target, which is how alerts are matched to CIs;
- `display_name`: a short label for panels (EDW, AppEvents - Student, …).

During an EDW outage it fires `TargetSystemUnavailable` plus three `IntegrationLastRunFailed` alerts:
the four signals that share one root cause.

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

Expected: `technology-one-sm` and `edw` READY with equal counts (24/12/12); each integration
`last_ok 1.0`.

### 2. Primary outage: stop the EDW

```bash
docker compose stop edw               # or: scripts/simulate-edw-outage.sh (waits 2 intervals)
```

Expected within two intervals (≈ 65 s):

- `edw` unreachable: `curl localhost:8002/health/ready` fails; a Prometheus scrape gives `up{job="edw"}=0`.
- `technology-one-sm`, `edw-db` and all three workers keep running (`docker compose ps`).
- Each worker:
  - `integration_last_run_success 0`;
  - `integration_runs_total{outcome="failure"}` increasing;
  - `integration_run_failures_total{error_type="target_connection_error",failed_system="edw"}` increasing.
- Each worker logs `"outcome": "failure", "target": "edw", "dependency": "edw", "error_type": "target_connection_error"`.
- Worker `/metrics` endpoints keep answering.

`edw` uses `restart: unless-stopped`, so a deliberate `docker compose stop edw` keeps it down until
you start it.

### 3. Recovery

```bash
docker compose start edw              # or: scripts/recover-edw.sh
```

Expected:
- `edw` is ready in a few seconds;
- within one interval, all three `last_run_success` gauges return to `1` and `consecutive_failures` to `0`;
- worker containers are not restarted.

### 4. Data update

```bash
scripts/update-record.sh students STU-0001 '{"last_name":"Renamed"}'
# raw form:
curl -X PATCH localhost:8001/api/students/STU-0001 \
  -H 'Authorization: Bearer dev-poc-token' -H 'Content-Type: application/json' \
  -d '{"last_name":"Renamed"}'
sleep 35; curl -s localhost:8002/api/students/STU-0001
```

Expected: after the next student run, the EDW record shows the new value with the same `updated_at`
as in Technology One (SM). The run log shows `"upsert": {"inserted": 0, "updated": 1, "unchanged": 23}`.

### 5. Isolation (optional)

| Command | Behaviour |
|---|---|
| `docker compose stop appevents-student` | The general and curriculum flows keep syncing on schedule, and the student worker's metrics endpoint disappears (scrape `up=0`). `docker compose start appevents-student` restores it. |
| `docker compose stop technology-one-sm` | All three flows fail with `error_type=source_connection_error`, `failed_system=technology-one-sm`. EDW stays healthy and keeps its data. Workers keep running and recover after `docker compose start technology-one-sm`. |
| `docker compose run --rm --no-deps appevents-general --once` | One ad-hoc general-flow run; exit code 0 on success, 1 on failure (used by tests). |

### 6. Reset (destroys all POC data)

```bash
scripts/reset.sh --yes
# raw form:
docker compose down -v && docker compose up --build -d
```

`down -v` **deletes both database volumes**:
- `technology-one-sm` is reseeded on the next start (deterministic, so IDs and values are identical);
- `edw` is repopulated by the first sync runs.

## Configuration

All settings have defaults in `compose.yaml`; override them in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_INTERVAL_SECONDS` | 30 | schedule period for each worker (fixed rate, first run on startup) |
| `HTTP_TIMEOUT_SECONDS` | 5 | per-request timeout |
| `HTTP_MAX_RETRIES` | 1 | retries per request after the first attempt |
| `HTTP_RETRY_BACKOFF_SECONDS` | 1 | backoff × attempt number |
| `SOURCE_STARTUP_WAIT_SECONDS` | 60 | bounded wait for source (Technology One (SM)) readiness before the first run |
| `SOURCE_BASE_URL` / `TARGET_BASE_URL` | `http://technology-one-sm:8000` / `http://edw:8000` | Docker DNS names |
| `API_TOKEN` | `dev-poc-token` | bearer token for write endpoints (empty disables) |
| `SOURCE_DB_*` / `TARGET_DB_*` | dev values | database names/users/passwords |
| `SOURCE_HOST_PORT`, `TARGET_HOST_PORT`, `APPEVENTS_*_METRICS_PORT` | 8001, 8002, 9101–9103 | host ports |

Worst-case detection time = 2 × interval + 2 × (2 attempts × timeout + backoff) ≈ 82 s with defaults.

## Tests

```bash
scripts/run-tests.sh                   # unit + integration (stack must be running)
scripts/run-tests.sh tests/unit        # unit only, no Docker needed (<1 s)
scripts/run-tests.sh tests/integration # Docker scenarios only (~5 min at the default 30 s interval)
```

The script creates `.venv` and installs `tests/requirements.txt` on first use.

The integration suite stops and starts `edw`, `technology-one-sm` and `appevents-student`. The
environment is left running even when a test fails:
- every test restores what it stopped in a `finally` block;
- a session finalizer runs `docker compose start` on all services.

If you changed `SYNC_INTERVAL_SECONDS` (etc.) in `.env`, export it too so the test deadlines match.
`scripts/run-tests.sh` does this automatically.

| ID | Covered by |
|---|---|
| AT-01 | `test_01_baseline::test_at01_all_services_operational` |
| AT-02 | `test_01_baseline::test_at02_seed_data_minimums` |
| AT-03 | `test_01_baseline::test_at03_initial_sync_replicates_every_collection` |
| AT-04 | `test_01_baseline::test_at04_repeated_syncs_are_idempotent` |
| AT-05 | `test_02_edw_outage_recovery::test_at05_only_edw_is_down` |
| AT-06 | `test_02_edw_outage_recovery::test_at06_each_integration_fails_independently` |
| AT-07 | `test_02_edw_outage_recovery::test_at07_failed_run_logs_identify_the_target` |
| AT-08 | `test_02_edw_outage_recovery::test_at08_automatic_recovery_without_worker_restarts` |
| AT-09 | `test_01_baseline::test_at09_source_changes_propagate` |
| AT-10 | `test_01_baseline::test_at10_…` and `test_02_edw_outage_recovery::test_at10_metrics_scrapeable_while_edw_down` |
| AT-11 | `test_03_isolation::test_at11_one_worker_down_does_not_affect_the_others` |
| AT-12 | this README + `scripts/run-tests.sh` |
| extra | `test_01_baseline::test_run_once_mode_exit_codes`, `test_03_isolation::test_source_outage_is_classified_as_source_failure`, `tests/unit/test_sync.py` (classification, retries, log fields) |

## Repository layout

```
compose.yaml, .env.example     one Compose project, defaults inline
app-technology-one-sm/         Technology One (SM) mock: FastAPI app, SQL migrations, deterministic seed
app-edw/                       EDW mock: FastAPI app, SQL migrations, bulk upsert
integrations/                  one worker image, run as the three appevents-* services via INTEGRATION=
tests/unit, tests/integration  pytest suites
scripts/                       up, status, outage, recovery, update, reset, run-tests, Jira Assets tools
docs/                          API contract, metrics/logs/rules, incident templates, Jira Assets model, requirements
observability/                 optional Prometheus config/rules and Grafana provisioning + dashboard
```

## Design decisions

- **One worker image, three services.** Same code, distinct `INTEGRATION`, job identity, container,
  metrics port and logs. They share nothing at runtime, so one failing cannot affect another.
- **Fresh HTTP client per run**, so no stale keep-alive connection survives an EDW restart.
- **Metrics server in its own thread** (`prometheus_client.start_http_server`), never blocked by sync work.
- **Migrations** are plain SQL files applied at startup and tracked in `schema_migrations`.
- **Upsert skips identical rows** (`IS DISTINCT FROM`), so `synced_at` only moves on real changes and
  the response reports inserted/updated/unchanged.
- **Deletes are not propagated**; records only ever upsert.
- **Replacing a mock with the real system:** point `SOURCE_BASE_URL` / `TARGET_BASE_URL` at the real
  system, or at an adapter that honours the contract in [docs/api.md](docs/api.md). The workers and
  telemetry are unchanged.
