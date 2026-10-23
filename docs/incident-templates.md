# Incident ticket templates

Short, table-style Jira descriptions, one per root-CI type. They hold only what is needed to
understand the impact and start the resolution. Every CI name links to its Jira Assets object
(through the ticket's Assets custom field), so anything else is one click away.

**Layout:** incident line → key fields → affected integrations → possible root cause → possible
resolution (future) → footer.

**Choosing a template:** Keep picks it from the **object type of the root CI**, after the Assets
lookup by `ci_name` and the precedence rules in [jira-assets/README.md](jira-assets/README.md):
- Application → [template 1](#1-application-system-template);
- Integration → [template 2](#2-integration-template);
- Connector (optional layer) → [template 3](#3-connector-template).

Placeholders are written `{like this}` and are filled in by Keep.

## 1. Application (system) template

Used when the root CI is an **Application**: a system is down, and the integrations reading from or
writing to it are just affected.

### Template

**🔴 {Application} unavailable · {N failing} integrations failing · {Priority} · since {start time}**

| | |
|---|---|
| **Affected CI** | [{Application}] · {health state} |
| **Resolver** | {Application Resolver Group} |
| **Impact** | {N affected} integrations {Source System} → {Application} stopped ({Domain} / {Subdomain}) |
| **Logs** | [Grafana · Loki: failed runs in this window] |

| Integration | State | Last success |
|---|---|---|
| [{Integration}] | FAILING / not monitored | {time} |
| … one row per affected integration … | | |

| **Possible root cause** | |
|---|---|
| **{Application}**, {confidence} | {evidence} |

| **Possible resolution** | *(future)* |
|---|---|

*Informed: {integrations' Resolver Groups}*
*Enriched from Jira Assets: {N} CIs matched ({1 application, N integrations}) · resolver, impact, source → target from Assets*

| Placeholder | Source |
|---|---|
| {Application}, health state | Alert `ci_name` → Assets Application · Prometheus `up` |
| Resolver Group, informed groups | Assets: Application and Integration **Resolver Group** |
| Affected integrations, N affected, Source System, Domain / Subdomain | Assets: integrations with `Target System` = {Application} (unmonitored ones included) |
| State, last success, N failing | Prometheus / alerts |
| Priority, confidence, evidence | Keep rules |

### Filled example

**🔴 EDW unavailable · 3 integrations failing · P2 · since 15:57**

| | |
|---|---|
| **Affected CI** | [EDW] · DOWN |
| **Resolver** | Data Squad |
| **Impact** | 4 integrations Technology One (SM) → EDW stopped (Current Students / Student Enrolment) |
| **Logs** | [Grafana · Loki: failed runs in this window] |

| Integration | State | Last success |
|---|---|---|
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student] | FAILING | 15:56 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum] | FAILING | 15:56 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW] | FAILING | 15:57 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Study Plan] | not monitored | – |

| **Possible root cause** | |
|---|---|
| **EDW**, high confidence | EDW health check failing; all monitored integrations report *target connection error*; source Technology One (SM) is healthy |

| **Possible resolution** | *(future)* |
|---|---|

*Informed: Current Students*
*Enriched from Jira Assets: 5 CIs matched (1 application, 4 integrations) · resolver, impact, source → target from Assets*

## 2. Integration template

Used when the root CI is the **Integration** itself: its target, source and connectors are
healthy, and nothing correlates.

### Template

**🟠 Integration failing: {short integration name} · {Priority} · since {start time}**

| | |
|---|---|
| **Affected CI** | [{Integration}] · FAILING ({N} runs) |
| **Resolver** | {Integration Resolver Group} |
| **Domain** | {Domain} / {Subdomain} |
| **Flow** | {Source System} → {Target System} · {Platform} · {Schedule} |
| **Connectors** | {Connectors} |
| **Error** | {error_type}: {error message} · last success {time} |
| **Logs** | [Grafana · Loki: this integration's failed runs] |

| **Possible root cause** | |
|---|---|
| **The integration itself**, {confidence} | {evidence} |

| **Possible resolution** | *(future)* |
|---|---|

*Enriched from Jira Assets: 1 CI matched (integration) · resolver, domain, flow, schedule, platform, connectors from Assets*

| Placeholder | Source |
|---|---|
| {Integration} | Alert `ci_name` → Assets Integration |
| Resolver Group, Domain / Subdomain, Source / Target System, Platform, Schedule, Connectors | Assets: Integration attributes (from the SOT) |
| N runs, error_type, error message, last success | Prometheus / alert labels |
| Priority, confidence, evidence | Keep rules |

### Filled example

**🟠 Integration failing: AppEvents from SMS to EDW - Student · P3 · since 15:57**

| | |
|---|---|
| **Affected CI** | [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student] · FAILING (3 runs) |
| **Resolver** | Current Students |
| **Domain** | Current Students / Student Enrolment |
| **Flow** | Technology One (SM) → EDW · Boomi · Every 5 minutes daily between 07:00 and 21:59 |
| **Connectors** | CONN - Data Warehouse (EDW) DB · CONN - Technology One Student SOAP - ADF Services |
| **Error** | validation_error: HTTP 422 from EDW · last success 15:42 |
| **Logs** | [Grafana · Loki: this integration's failed runs] |

| **Possible root cause** | |
|---|---|
| **The integration itself**, medium confidence | EDW is healthy; other integrations writing to EDW are succeeding; this one has failed 3 runs in a row with the same validation error |

| **Possible resolution** | *(future)* |
|---|---|

*Enriched from Jira Assets: 1 CI matched (integration) · resolver, domain, flow, schedule, platform, connectors from Assets*

## 3. Connector template

Only when the integrations involved have **Connector CIs** (the optional layer), the connector's
heartbeat is failing, and the application it connects to is healthy.

### Filled example

**🔴 Connector failing: CONN - Data Warehouse (EDW) DB · 3 integrations failing · P2 · since 15:57**

| | |
|---|---|
| **Affected CI** | [CONN - Data Warehouse (EDW) DB] · heartbeat missing since 15:57 |
| **Connects to** | [EDW] · healthy |
| **Resolver** | Integration & Platforms |
| **Impact** | 4 integrations use this connector (Current Students / Student Enrolment) |
| **Logs** | [Grafana · Loki: connector + integration errors] |

| Integration | State | Last success |
|---|---|---|
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student] | FAILING | 15:56 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum] | FAILING | 15:56 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW] | FAILING | 15:57 |
| [ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Study Plan] | not monitored | – |

| **Possible root cause** | |
|---|---|
| **Connector**, high confidence | Heartbeat missing; every failing integration uses this connector; EDW health check is OK |

| **Possible resolution** | *(future)* |
|---|---|

*Informed: Current Students, Data Squad*
*Enriched from Jira Assets: 5 CIs matched (1 connector, 4 integrations) · resolver, impact, connects-to from Assets*

## Rules for all templates

- **Links:** every CI name is a link to its Assets object.
- **Status rows:** show *Status* only when it isn't Production (e.g. "Production - To Retire").
- **Failed lookups:** if the Assets lookup fails, the footer says so ("CI 'X' not found in Assets;
  routed to the default group") and the ticket goes to a default group.
- **Updates:** a new failing integration, a change of root cause or a resolution is added as a short
  comment table. The description isn't rewritten.
- **Header icons:** 🔴 for an application or connector root CI (several integrations affected), 🟠
  for a single integration.
- **Jira formatting:** in a real description these tables are Jira tables (`|| Field || Value ||`),
  filled in by Keep.
