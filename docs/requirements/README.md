# Education Integration Failure Simulation — Build Requirements

**Version:** 1.0
**Purpose:** Coding-agent implementation specification
**Scope:** Application A, Application B, and three independent scheduled integrations only

> Converted from `education_integration_poc_requirements.docx`. Wording is unchanged; only the formatting is now Markdown.

## 1. Objective

Build a fully working, locally runnable education-themed integration environment. The environment must provide two independent applications and three scheduled data synchronization jobs. It will later serve as a deterministic testbed for an **external** observability / alert-correlation proof of concept.

**Primary demonstration:** When Application B becomes unavailable, its own health check fails and all three integration jobs fail independently because they cannot reach their destination. This produces four distinct observable failure conditions sharing one root cause. When B returns, the jobs must recover automatically.

**Important:** Do **not** build an alert-correlation engine, Jira integration, Prometheus server, Grafana, Alertmanager, AI/ML logic, or a production-grade integration platform as part of this task.

## 2. Implementation decision

Implement two small, containerized **mock education applications**, not full Moodle or another production LMS. Use a simple Python/FastAPI API for each, with PostgreSQL storage (one distinct database/container per app, or independent databases with clear isolation). This intentionally prioritizes quick, reliable POC setup over real-world product features.

- **Application A — Mock Student Information System (SIS):** authoritative source of synthetic student, staff, and course records.
- **Application B — Mock Learning Management System (LMS):** receives and stores replicated student, staff, and course records.
- **Integration 1 — Student Sync:** transfers students from SIS to LMS.
- **Integration 2 — Staff Sync:** transfers staff from SIS to LMS.
- **Integration 3 — Course Sync:** transfers courses from SIS to LMS.

The mock LMS API and data contracts should be documented so Moodle could replace it in a *future* iteration without changing the observability scenario.

## 3. Deliverable and quality goals

A coding agent must deliver a complete repository that can be started using **one command** (`docker compose up --build -d`), without manual database initialization, user interface setup, API token creation, or cloud services. Dependencies must be open source and run locally in Docker. Provide all source files, configuration, database migrations/seeding, documentation, and tests. No TODO placeholders or pseudocode in the delivered solution.

### 3.1 Expected repository layout

```
education-integration-poc/
  compose.yaml
  .env.example
  README.md
  app-a-sis/                 # source API and seed data
  app-b-lms/                 # target API
  integrations/              # 3 independent schedulers/workers
  tests/                     # automated tests and failure scenario
  scripts/                   # reset, reseed, simulate failure/recovery
  docs/                      # API and metrics reference
```

Alternative layouts are acceptable if the same requirements are met.

## 4. Functional requirements

### FR-01 — Source application A (SIS)

- Run as a separate Docker service (`sis`) exposing an HTTP API and persistent data store.
- Seed **at least 20 students, 10 staff members, and 10 courses** with clearly synthetic names/emails and deterministic IDs. No actual personal information.
- Provide independently queryable collections:
  - `GET /api/students`
  - `GET /api/staff`
  - `GET /api/courses`
  - `GET /health/live` and `GET /health/ready`
  - `GET /metrics` in Prometheus text exposition format
- Supply stable external IDs, an `updated_at` timestamp, and relevant fields:
  - Student: `student_id`, `first_name`, `last_name`, `email`, `status`, `updated_at`.
  - Staff: `staff_id`, `first_name`, `last_name`, `email`, `department`, `status`, `updated_at`.
  - Course: `course_id`, `code`, `title`, `department`, `status`, `updated_at`.
- Provide a documented test-friendly method to update a source record (e.g., `PATCH /api/students/{id}`, similarly for staff/courses), so a subsequent sync demonstrably propagates changes.

### FR-02 — Target application B (LMS)

- Run as a **separate Docker service** (`lms`) exposing its own HTTP API and persistent database.
- Provide three independent idempotent bulk-upsert endpoints:
  - `PUT /api/students/bulk`
  - `PUT /api/staff/bulk`
  - `PUT /api/courses/bulk`
- Provide independent read-back endpoints (`GET /api/students`, `/api/staff`, `/api/courses`) to verify replicated data.
- Provide `GET /health/live`, `GET /health/ready`, and `GET /metrics`.
- Accept records keyed by the immutable source ID; repeated sync must update existing records rather than create duplicates.
- Report predictable API errors (appropriate HTTP status, JSON response with error code/message). Validate input and avoid silently accepting malformed payloads.
- **Critical failure behavior:** Stopping just the `lms` Docker container must make the target unavailable while the SIS and all three integrations continue running and attempting syncs.

### FR-03 — Three independent scheduled integrations

- Implement the three integrations as **three independently identifiable Docker services** (`sync-students`, `sync-staff`, `sync-courses`) or equivalent independently operating workers, each with distinct job identity and metrics.
- All jobs run automatically every **30 seconds by default**, configurable by environment variable; they must also support a **run-once** mode for tests.
- Each job reads *only its own data type* from the SIS and writes it to the matching LMS endpoint.
- The jobs must be decoupled: failure in students must not stop staff or courses, and no integration may depend on the success of another.
- Make HTTP connections using Docker DNS service names (`http://sis:...`, `http://lms:...`) rather than `localhost` within containers.
- Set a configurable, short HTTP timeout (default **5 seconds**) and a bounded retry policy (default **1 retry**, brief backoff). Count a schedule execution as **one failed run** after retry exhaustion; log each attempt separately if desired.
- On failed runs, log the failing operation, target system, error type, HTTP status if available, job name, and run ID. Continue scheduling subsequent runs without crashing permanently.
- On recovery, succeed automatically on the next schedule without restarting the integration containers.
- Syncs must be idempotent; the destination count should stay constant across repeated successful runs when the source is unchanged.

### FR-04 — Configurable scenario controls

Provide documented scripts/commands for these scenarios:

- **Baseline:** `docker compose up --build -d`; wait for healthy services; inspect target data and latest job runs.
- **Primary outage:** `docker compose stop lms`; wait two sync intervals; show that target health fails and **all three jobs independently fail**.
- **Recovery:** `docker compose start lms`; wait for readiness and a sync interval; all jobs succeed automatically.
- **Data update:** modify one synthetic SIS record, wait for the relevant sync, then verify it was updated in LMS.
- **Optional isolation tests:** stop `sis` or cause one integration to fail without affecting other integration processes; document resulting behavior.
- **Reset:** provide a documented command to wipe/reseed all synthetic data (`docker compose down -v` plus normal startup is acceptable; warn that it destroys POC data).

Avoid orchestrator behavior that automatically restarts a deliberately stopped LMS; `docker compose stop lms` must reliably keep it down until manually restarted.

## 5. Telemetry and observability contracts

**Only expose telemetry; do not deploy monitoring or alerting infrastructure.** All signals must allow an external observability system to conclude that the LMS outage and three sync failures occur in the same time period and share a dependency.

### FR-05 — Health and Prometheus metrics

- Both applications expose `/metrics`. All three jobs must expose a scrapeable `/metrics` endpoint on **unique ports**, even while their last runs have failed. Prefer a lightweight HTTP metrics server in each worker.
- Use standard Prometheus client libraries and readable metrics names. At minimum:

| Metric | Type | Key labels | Meaning |
|---|---|---|---|
| `app_up` | Gauge | `app` | Application process and readiness indicator, 1 healthy, 0 unhealthy when process responds; a stopped container is detected externally as `up=0` / scrape failure. |
| `integration_last_run_success` | Gauge | `integration`, `source`, `target` | 1 if latest completed run succeeded; 0 if failed; **do not reset between runs**. |
| `integration_runs_total` | Counter | `integration`, `outcome` | Count of completed scheduled runs (`success` or `failure`). |
| `integration_last_run_timestamp_seconds` | Gauge | `integration` | Unix timestamp of the most recently completed run. |
| `integration_run_duration_seconds` | Histogram | `integration` | Sync duration distribution. |
| `integration_records_synced_total` | Counter | `integration` | Number of records successfully sent. |

- Metrics must remain available when the destination LMS is down. Never make a worker's `/metrics` endpoint depend on a successful LMS request.
- Include **static identifiers** on every integration signal: `integration=students|staff|courses`, `source=sis`, `target=lms`. The target's corresponding service identity is `lms`. For logs include `dependency=lms` to make the causal relationship discoverable later.
- Avoid high-cardinality labels such as run IDs, email addresses, exception text, or record IDs on Prometheus series. Keep run IDs in logs only.
- Document a concise suggested future monitoring rule set without installing it: target unavailable when Prometheus `up{job="lms"} == 0`; latest integration failure when `integration_last_run_success == 0` after at least one completed run. Clearly distinguish exporters' `up` metric from application-generated `app_up`.

### FR-06 — Structured logs

- Emit one JSON log entry for each completed job run, with: `timestamp`, `level`, `service`, `integration`, `source`, `target`, `dependency`, `run_id`, `outcome`, `duration_ms`, `records`, `error_type`, and `error_message` (where applicable).
- Use distinct error classifications, e.g., `target_connection_error`, `target_timeout`, `target_http_error`, `source_connection_error`, `validation_error`.
- Ensure stopped-LMS errors are explicitly recognized as **target** failures rather than generic job failures.

## 6. Non-functional requirements

- **Local-first:** one Docker Compose project; no required internet calls at runtime beyond pulling/building container dependencies on first setup.
- **Cross-platform:** support Docker Desktop on Windows/macOS and Docker Engine on Linux.
- **Simple setup:** no manual migrations, no interactive prompt, no credentials required beyond values supplied by `.env.example` (non-production defaults only).
- **Startup resilience:** jobs wait/retry for SIS startup, but continue to operate even if LMS is unavailable; do not require LMS to be healthy before worker containers can start.
- **Clear service names:** `sis`, `lms`, `sync-students`, `sync-staff`, `sync-courses`, plus storage containers as required.
- **Durability:** records persist across ordinary container restarts; no persistence required for job run history beyond counters/current status and logs.
- **Security:** synthetic-only data; expose ports to localhost by default; API may use a simple shared development token if that does not undermine one-command setup. Document that the environment is not production-hardened.
- **Resource use:** target a development laptop with 8 GB RAM; avoid Kubernetes, Kafka, Java-based integration suites, external cloud dependencies, and unnecessary UI frameworks.
- **Failure determinism:** status changes must be observable within two scheduler intervals plus timeout/retry budget.

## 7. Docker Compose and configuration

- Provide port mappings documented in the README, with no port conflicts. Suggested: SIS `localhost:8001`, LMS `localhost:8002`, job metrics `localhost:9101`, `9102`, `9103` respectively (internal container ports may differ). Database ports do not need to be host-exposed.
- Provide configurable values in `.env.example`: `SYNC_INTERVAL_SECONDS=30`, `HTTP_TIMEOUT_SECONDS=5`, `HTTP_MAX_RETRIES=1`, API base URLs, database credentials, and any API token. Default configuration must work without editing `.env`.
- Define container health checks for SIS and LMS. Workers must remain alive and expose metrics even when LMS is stopped. Use bounded startup retries and graceful shutdown.

## 8. Automated tests and acceptance criteria

| ID | Test | Pass criteria |
|---|---|---|
| AT-01 | One-command start | `docker compose up --build -d` completes; SIS, LMS, and three syncs become operational without manual steps. |
| AT-02 | Seed data | SIS contains >=20 students, >=10 staff, >=10 courses. |
| AT-03 | Initial sync | Within 2 sync intervals, LMS contains all seeded records in correct collections. |
| AT-04 | Idempotency | Two extra successful cycles do not increase LMS record counts or create duplicate source IDs. |
| AT-05 | Outage | Stop `lms` only. SIS and all workers remain up; LMS is inaccessible. |
| AT-06 | Four distinct failure conditions | External inspection can see LMS down plus `integration_last_run_success=0` independently for students, staff, and courses. Job counters increase with `outcome="failure"`. |
| AT-07 | Failure identity | Each worker's failed-run log includes `target=lms`, `dependency=lms`, integration name, and an appropriate target error classification. |
| AT-08 | Automatic recovery | Start `lms`; within 2 sync intervals after readiness, all three last-run-success gauges return to 1 without restarting workers. |
| AT-09 | Change propagation | Updating a single student, staff, or course in SIS propagates to LMS in the next relevant run. |
| AT-10 | Metrics remain scrapeable | All three worker metrics endpoints still return valid Prometheus text when LMS is stopped. |
| AT-11 | Independent execution | A failure of one worker does not crash or suppress execution of the other two. |
| AT-12 | Tests and README | Automated baseline and outage/recovery tests are runnable; README documents exact commands and expected outcomes. |

## 9. Output expected from the coding agent

Deliver the complete functioning project, including:

- Source code, Dockerfiles, `compose.yaml`, default configuration, migrations, synthetic seed generator/data.
- A README with prerequisites, one-command startup, ports/endpoints, how to view data, and how to run all five scenario controls.
- Integration tests (including a script that stops and restarts LMS and **restores the environment** even when a test fails).
- An API contract and metric catalogue listing exact metric names, types, labels, and examples.
- A final implementation summary stating what was built, the exact commands used to test it, the actual test results, and any limitations. Do not claim tests were run unless they actually were.

## 10. Explicit exclusions and future extension

**Excluded:** alert grouping/correlation, root-cause engine, incident creation, Jira, Grafana dashboards, Prometheus/Alertmanager deployments, tracing stack, OpenTelemetry collectors, AI agents, Moodle installation, real student data, authentication integrations, enrollment/business-process complexity, and course content.

**Future compatibility:** Keep source/target identifiers consistent, interfaces documented, and metrics standard. In a later POC the team may replace `lms` with Moodle or introduce a richer student system, while retaining the three job identities and the same failure demonstration.

## Implementation instruction for the coding agent

Build the complete repository specified above. Prioritize a deterministic, minimal Docker-based implementation over feature-rich applications. Do not add observability infrastructure or a correlation engine: only expose health, Prometheus-compatible metrics, and structured logs. Run the test suite and demonstrate the stopped-LMS and recovered-LMS scenarios. If anything is ambiguous, adopt the simplest solution that satisfies the acceptance criteria and document the decision.
