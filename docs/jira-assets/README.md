# Jira Assets model for alert enrichment

Keep enriches each alert by looking up its Prometheus `job` label in Jira Assets. It then follows
references to find the root cause (platform, connector or application), its owner, and everything
else affected. The model combines the Integrations SOT (`Integrations SOT(Integrations).csv`) with
ownership, which the SOT does not record.

| File | Contents |
|---|---|
| `00-object-type-attributes.csv` | Specification of the 7 object types and 79 attributes: type, reference type, cardinality, unique/required, example, and **Source** (SOT column, provided, inferred or new). A checklist for creating them in the UI, not an import file. |
| `01-teams.csv` | Current Students, Data Squad, Integration & Platforms |
| `02-business-services.csv` | Student Administration / Core Data (domain Current Students) |
| `03-integration-platforms.csv` | Boomi Atom (Production) |
| `04-applications.csv` | Technology One (SM), EDW |
| `05-connectors.csv` | The 5 Boomi connectors used by the DXMULTI-0001 flows and their modules |
| `06-integrations.csv` | The 4 ETL - DXMULTI-0001 AppEvents flows: Student, Curriculum, Study Plan, and the unsuffixed general flow |
| `07-integration-components.csv` | The 15 RPS - DXMULTI-0001 AppEvent modules, one per data entity |
| [`../../scripts/preview-assets-graph.py`](../../scripts/preview-assets-graph.py) | Offline check of the CSVs, and a text view of the resulting graph |
| [`../../scripts/test-assets-lookup.py`](../../scripts/test-assets-lookup.py) | Read-only check that the lookups Keep needs return the right answers |

## The model

```
Team ◀─Owned by── Business Service ◀─Part of── Application ◀─Connects to── Connector ──Runs on─▶ Integration Platform
                                                   ▲  ▲                         ▲                       ▲
                                     Reads from ───┘  └─── Writes to            │ Uses                  │ Runs on
                                                   Integration ─────────────────┴───────────────────────┘
                                                        ▲
                                                        └── Called by ── Integration Component (one per data entity)
```

| Object type | Why it exists | Key attributes |
|---|---|---|
| **Team** | Routing and P1 escalation | Assignment group, chat channel, on-call rotation, escalation manager |
| **Business Service** | Business impact and domain | Domain, SOT subdomain, criticality, owner |
| **Integration Platform** | One root cause for every flow on it, e.g. a Boomi Atom outage | Platform type, host, hosting, owner |
| **Application** | Source and target systems | Monitoring Job, owner, vendor, vendor support, hosting, support hours, status page, tier |
| **Connector** | A shared dependency. A broken credential or endpoint looks like a target outage to every flow that uses it. | Type, connects to, platform, endpoint, auth type, credential owner, **credential expiry** |
| **Integration** | The monitored flow | Integration ID, Monitoring Job, source/target, platform, **connectors (multi)**, schedule (interval, active window, timezone), expected volume, status, legacy, log links, repo, SOT confidence, tier |
| **Integration Component** | Shows which data goes stale | Data entity, called by, connectors |

### Layered root cause

With platforms and connectors in the graph, Keep can identify the narrowest shared cause behind a
set of failing flows:

| Pattern | Root CI | Ticket goes to |
|---|---|---|
| Every flow on the Boomi Atom fails | Integration Platform | Integration & Platforms |
| Only flows using one connector fail, e.g. with auth errors | Connector | Credential owner / connected application's owner |
| `up{job="lms"} == 0` plus target errors on its flows | Application EDW | Data Squad |
| One flow fails alone | Integration | Current Students |

Use these lookups for the blast radius:
- `objectType = "Integration" AND "Target Application" = "EDW"` gives the flows that write to an application.
- `objectType = "Integration" AND "Connectors" = "CONN - Data Warehouse (EDW) DB"` gives the flows that share a connector.
- `objectType = "Integration Component" AND "Called By" = "<flow>"` gives the data entities a failed flow leaves stale.

### Schedule-aware alerting

The real DXMULTI-0001 flows run **every 5 minutes, 07:00–21:59 (Australia/Sydney)**. Alert rules
and Keep should use **Interval Minutes** and **Active Window Start/End**:
- no "stale" alerts outside the window;
- staleness threshold ≈ 2 × interval.

The POC workers run every 30 s around the clock, so these values describe production, not the mock.

## Names vs. monitoring keys

Object names match the SOT exactly, so the two can be joined. **Monitoring Job** stays equal to the
Prometheus `job` label that the POC stack emits:

| Assets object | Monitoring Job | POC service |
|---|---|---|
| Technology One (SM) | `sis` | `sis` (mock source) |
| EDW | `lms` | `lms` (mock target) |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student | `sync-students` | `sync-students` |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum | `sync-courses` | `sync-courses` |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW | `sync-staff` | `sync-staff` (stands in for the general flow) |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Study Plan | *(empty)* | not in the POC |

Monitoring Job is optional on Integrations and Platforms, and unique wherever it is set. The
validator also checks it is unique across all object types, because Keep's lookup searches the
whole schema. When alerts come from the real systems, set it to the real `job` labels.

## Where the values came from: confirm before importing

| Status | Values |
|---|---|
| **From the SOT** | Integration names, ID, platform, process type, pattern, method, schedule, volume/week, status, legacy, log source, SOT confidence; connector names and which flow/module uses which; module data entities; domain and subdomain |
| **Provided by you** | Team names, application and flow owners, business service name, flow descriptions |
| **Confirmed** | Which ETL flow calls which RPS module (**Called By**), grouped by data entity |
| **Inferred: please confirm** | The Study Plan description, which I drafted. The connector types. Timezone Australia/Sydney. |
| **Placeholders** | Team emails and assignment groups, and the *Integration & Platforms* team as the Boomi owner |
| **Left empty, to fill in** | Chat channels, on-call rotations, escalation managers (User attributes, set in the UI), vendor support contacts, hosting, support hours, status pages, connector endpoints, auth types, credential owners and expiry, Kibana and Boomi Process Reporting links, repo URLs |

Known gaps:
- **Subdomain mismatch:** the SOT says **Student Enrolment** but the business service is named
  *Student Administration / Core Data*. It's kept in `SOT Subdomain` until one is chosen.
- **Omitted connectors:** `RPS … StudentStudyPackageState` also uses `CONN - Dynamics 365 OData` and
  `CONN - Dynamics 365 HTTP`. They're left out until a Dynamics 365 application and owner are added.
- **No owners in the SOT:** the SOT has no owner column, and 65 production rows have no source or target.

## 1. Create the schema and object types (UI)

CSV import loads *objects*. Object types and attributes are defined in the schema first:

1. **Assets → Create schema →** empty schema named **`Observability POC`**. If you use a different
   name, set `ASSETS_SCHEMA` for the test script.
2. **Schema settings → Reference types:** create `Owned by`, `Part of`, `Reads from`, `Writes to`,
   `Connects to`, `Runs on`, `Uses`, `Called by`. Give *Writes to* and *Uses* distinct colours, so the
   blast radius stands out in the graph view.
3. Create the object types **in this order**, because an object reference needs its target type to
   exist first: **Team → Business Service → Integration Platform → Application → Connector →
   Integration → Integration Component**.
4. Add the attributes from `00-object-type-attributes.csv`:
   - `Name` already exists and is the label attribute.
   - **Monitoring Job**: Text, **Unique**. Required on Application only.
   - Object attributes: pick the referenced object type and reference type. For **Cardinality =
     Multiple** (`Connectors`), set the attribute's maximum to unlimited.
   - Select attributes: add the listed options.
   - Integer / Float / Date: use those Assets types. For **Time (HH:MM)**, use Text.

## 2. Import the objects (CSV import)

**Schema → Import → Create import → CSV import.** Create one import mapping per file and run them in
file order (01 → 07).

- Delimiter `,`, encoding UTF-8, first row is the header. Map each column to the attribute with the same name.
- Set **identifier = `Name`** for every mapping, so re-running an import updates objects instead of
  duplicating them.
- **Reference columns** hold the referenced object's **Name**. Map them with the AQL placeholder
  `Name = ${<column>}` against the referenced type. For example, `Owner Team` uses `Name = ${Owner Team}`
  against Team.
- **Multi-value cells** (`Connectors`) separate values with ` | `. Set the import's multi-value
  separator to `|` and check it on one Integration first. If your Assets version doesn't split the
  cell, import the first connector and add the rest in the UI, or ask me for a one-row-per-reference
  variant.

Run the offline check first:

```bash
python3 scripts/preview-assets-graph.py
```

It validates columns, required and unique values, select options, Integer/Float/Date/Time/URL values,
every reference including multi-value ones, import order, and Monitoring Job uniqueness across types.
Then it prints the graph. It currently reports 31 objects and 76 references.

## 3. Test the lookups

Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens. Use a service
account with Assets access to the schema, the same account Keep will use. Add the values to `.env`
(gitignored) or export them:

```bash
JIRA_SITE=https://<your-site>.atlassian.net
JIRA_EMAIL=svc-observability@example.com
JIRA_API_TOKEN=<token>
# ASSETS_SCHEMA="Observability POC"
```

```bash
python3 scripts/test-assets-lookup.py
```

The script only reads, never writes. The Assets workspace ID is discovered automatically, or set
`ASSETS_WORKSPACE_ID`. For Jira Data Center, set `JIRA_DEPLOYMENT=datacenter` and `JIRA_PAT`; older
Insight versions may need `ASSETS_API_BASE=https://<host>/rest/insight/1.0`. Exit codes: `0` passed,
`1` a check failed, `2` configuration, auth or connectivity error.

It checks, using the CSVs as the expected state:

1. Each Monitoring Job (`sis`, `lms`, `sync-students`, `sync-courses`, `sync-staff`) returns exactly
   one object, with the expected type, name, owner, business service, integration ID/key,
   source/target, platform and connector set. Study Plan is skipped because it has no Monitoring Job.
2. Blast radius by target application: all 4 flows write to EDW. Blast radius by connector: each
   connector is used by the expected flows, e.g. the EDW DB connector by all 4.
3. Correlation: alerts from `lms` plus the three monitored flows all resolve to root CI **EDW**, owned
   by **Data Squad**.

## How correlation uses this

| Alert | Keep looks up | Root CI rule | Root CI |
|---|---|---|---|
| `up{job="lms"} == 0` | `"Monitoring Job" = "lms"` → Application EDW | an Application is its own root | EDW |
| `integration_last_run_success == 0`, `job="sync-students"` | → AppEvents - Student | `failed_system=lms` → the Application with Monitoring Job `lms` | EDW |
| the same for `sync-courses` and `sync-staff` | → Curriculum, general flow | same | EDW |

All four alerts become one incident, routed to the root CI's **Owner Team** (Data Squad). Current
Students owns the flows and the source system, so it is informed rather than assigned.

The ticket can include:
- the blast radius: all 4 flows, including the unmonitored Study Plan;
- the stale data entities, from the Integration Components;
- the shared connectors, with credential owner and expiry;
- the platform;
- log links and runbooks.

If the source fails instead (`failed_system=sis`), the root is Technology One (SM) and the ticket goes
to Current Students.

The Assets API identifies attributes by numeric ID in its responses. The script resolves the names,
either from the response or from `GET /objecttype/{id}/attributes`. Keep's HTTP step needs the same
mapping, or a thin adapter that returns flat JSON (`owner`, `root_ci`, `runbook_url`, …).
