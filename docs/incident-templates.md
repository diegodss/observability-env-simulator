# Incident ticket templates

Short, table-style Jira descriptions, one per root-CI type. They hold only what is needed to
understand the impact and start the resolution. Every CI name links to its Jira Assets object
(through the ticket's Assets custom field), so anything else is one click away.

**Layout:** incident line → key fields → affected integrations → possible root cause → possible
resolution (future) → footer.

**Choosing a template:** Keep picks it from the **object type of the root CI**, after the Assets
lookup by `ci_name` and the precedence rules in [jira-assets/README.md](jira-assets/README.md):
- Application → [template 1](#1-application-root-ci);
- Connector (optional layer) → [template 2](#2-connector-root-ci);
- Integration → [template 3](#3-integration-root-ci).

## Where the values come from

| Value | Source |
|---|---|
| Affected CI, root CI type | Alert `ci_name` → Assets lookup by Name |
| Resolver, informed groups | Assets: **Resolver Group** of the root CI / of the affected integrations |
| Impact list (including unmonitored integrations) | Assets: integrations with `Target System` = the app, or `Connector CIs` = the connector |
| Domain / Subdomain, flow, schedule, platform, connectors | Assets: Integration attributes (from the SOT) |
| State, last success, error | Prometheus / alert labels (`error_type`, `failed_system`) |
| On call | JSM on-call schedule for the Resolver Group |
| Logs | Grafana Explore link with a Loki query for the incident window |
| Priority | Rules: affected count, Status, inside the schedule window, Legacy |

## 1. Application root CI

**🔴 EDW unavailable · 3 integrations failing · P2 · since 15:57**

| | |
|---|---|
| **Affected CI** | [EDW] · DOWN |
| **Resolver** | Data Squad · on call: &lt;name&gt; |
| **Impact** | 4 integrations Technology One (SM) → EDW stopped (Current Students / Student Enrolment) |
| **Logs** | [Grafana · Loki: failed runs in this window] |

| Integration | State | Last success |
|---|---|---|
| [AppEvents from SMS to EDW - Student] | FAILING | 15:56 |
| [AppEvents from SMS to EDW - Curriculum] | FAILING | 15:56 |
| [AppEvents from SMS to EDW] | FAILING | 15:57 |
| [AppEvents from SMS to EDW - Study Plan] | not monitored | – |

| **Possible root cause** | |
|---|---|
| **EDW**, high confidence | EDW health check failing; all monitored integrations report *target connection error*; source Technology One (SM) is healthy |

| **Possible resolution** | *(future)* |
|---|---|

*Informed: Current Students · on call: &lt;name&gt;*
*Enriched from Jira Assets: 5 CIs matched (1 application, 4 integrations) · resolver, impact, source → target from Assets*

## 2. Connector root CI

Only when the integrations involved have **Connector CIs** (the optional layer).

**🔴 Connector failing: CONN - Data Warehouse (EDW) DB · 3 integrations failing · P2 · since 15:57**

| | |
|---|---|
| **Affected CI** | [CONN - Data Warehouse (EDW) DB] · heartbeat missing since 15:57 |
| **Connects to** | [EDW] · healthy |
| **Resolver** | Integration & Platforms · on call: &lt;name&gt; |
| **Impact** | 4 integrations use this connector (Current Students / Student Enrolment) |
| **Logs** | [Grafana · Loki: connector + integration errors] |

| Integration | State | Last success |
|---|---|---|
| [AppEvents from SMS to EDW - Student] | FAILING | 15:56 |
| [AppEvents from SMS to EDW - Curriculum] | FAILING | 15:56 |
| [AppEvents from SMS to EDW] | FAILING | 15:57 |
| [AppEvents from SMS to EDW - Study Plan] | not monitored | – |

| **Possible root cause** | |
|---|---|
| **Connector**, high confidence | Heartbeat missing; every failing integration uses this connector; EDW health check is OK |

| **Possible resolution** | *(future)* |
|---|---|

*Informed: Current Students, Data Squad · on call: &lt;names&gt;*
*Enriched from Jira Assets: 5 CIs matched (1 connector, 4 integrations) · resolver, impact, connects-to from Assets*

## 3. Integration root CI

No correlation: the integration's own target and connectors are healthy. The key fields add the
Assets context needed to understand one integration on its own.

**🟠 Integration failing: AppEvents from SMS to EDW - Student · P3 · since 15:57**

| | |
|---|---|
| **Affected CI** | [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student] · FAILING (3 runs) |
| **Resolver** | Current Students · on call: &lt;name&gt; |
| **Domain** | Current Students / Student Enrolment |
| **Flow** | Technology One (SM) → EDW · Boomi · every 5 min, 07:00–21:59 |
| **Connectors** | CONN - Data Warehouse (EDW) DB · CONN - Technology One Student SOAP - ADF Services |
| **Error** | validation_error: HTTP 422 from EDW · last success 15:42 |
| **Logs** | [Grafana · Loki: this integration's failed runs] |

| **Possible root cause** | |
|---|---|
| **The integration itself**, medium confidence | EDW is healthy; other integrations writing to EDW are succeeding; this one has failed 3 runs in a row with the same validation error |

| **Possible resolution** | *(future)* |
|---|---|

*Enriched from Jira Assets: 1 CI matched (integration) · resolver, domain, flow, schedule, platform, connectors from Assets*

## Conventions

- **Header icons:** 🔴 for an application or connector root CI (several integrations affected), 🟠 for a single integration.
- **Status rows:** show *Status* only when it isn't Production (e.g. "Production - To Retire").
- **Updates:** a new failing integration, a change of root cause or a resolution is added as a short
  comment table. The description isn't rewritten.
- **Failed lookups:** the footer says so ("CI 'X' not found in Assets; routed to the default group"),
  so CMDB gaps show up.
- **Jira formatting:** in a real description these tables are Jira tables (`|| Field || Value ||`),
  filled in by Keep.
