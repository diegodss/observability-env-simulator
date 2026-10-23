# Jira Assets: Integrations schema (and a proposed Applications schema)

Alerts carry the exact Assets object Name of the failing CI (`ci_name`). Keep looks that name up,
follows **Target System** to find the root CI, and routes the ticket to the root CI's
**Resolver Group**.

## Principles

1. **The SOT is a one-off initial load.** `Integrations SOT(Integrations).csv` seeds Assets once.
   From then on **Jira Assets is the source of truth**, and changes are made there, not in the SOT.
2. **Two schemas (option A).**
   - **Integrations** is owned by the integration owner (you).
   - **Applications** is a *proposed* schema for the systems that integrations read from and write to.
   - Integrations reference Applications across schemas. If the CMDB team already has an application
     type, re-point the two references and drop the proposed schema (see
     [Adopting the CMDB's application schema](#adopting-the-cmdbs-application-schema)).
3. **Mirror the SOT, invent almost nothing.**
   - Integration attributes copy the SOT column names verbatim.
   - The only additions are **Description** and **Resolver Group**, plus **Aliases** and **Status**
     on Application.
4. **Built-in Assets types where they exist.** **Group** for Resolver Group (a real Jira group) and
   **Status** for lifecycle.
5. **Every name is a placeholder.** Schema, object type, attribute and reference-type names can be
   renamed later to match the organisation's standard.

## Files

| File | Purpose |
|---|---|
| `initial-load/scope-integrations.csv` | Which SOT integrations to load, plus Resolver Group and Description (not in the SOT) |
| `initial-load/scope-applications.csv` | The applications in scope: canonical Name, Aliases (SOT spellings), Resolver Group, Status, Description |
| `initial-load/scope-connectors.csv` | **Optional.** Connectors that exist as CIs (e.g. send a heartbeat): Connects To, Resolver Group, Status, Description |
| `applications-schema/00-object-type-attributes.csv` | Spec for the proposed **Applications** schema (6 attributes) |
| `applications-schema/01-applications.csv` | Import file, generated |
| `integrations-schema/00-object-type-attributes.csv` | Spec for the **Integrations** schema: Integration (27 attributes, 21 straight from the SOT) and the optional Connector (6) |
| `integrations-schema/01-connectors.csv` | Import file, generated **only if** `scope-connectors.csv` exists |
| `integrations-schema/02-integrations.csv` | Import file, generated |
| [`../../scripts/sot-initial-load.py`](../../scripts/sot-initial-load.py) | Generates the two import files from the SOT and the scope files. Run once. |
| [`../../scripts/preview-assets-graph.py`](../../scripts/preview-assets-graph.py) | Offline check of the import files, and a text view of the graph |
| [`../../scripts/test-assets-lookup.py`](../../scripts/test-assets-lookup.py) | Read-only check against your Assets workspace |

Demo scope:
- the 4 `ETL - DXMULTI-0001` flows;
- the 2 applications they connect, Technology One (SM) and EDW;
- 2 of their connectors as CIs: the EDW DB and Technology One ADF connectors. `CONN - Boomi AtomSphere API`
  is deliberately left as text only, to show partial coverage.

The ticket layouts that use this data are in [../incident-templates.md](../incident-templates.md).

## Placeholder standards (rename freely)

| Standard | Placeholder | Notes |
|---|---|---|
| Schema names | `Integrations`, `Applications` | Set `ASSETS_INTEGRATION_SCHEMA` / `ASSETS_APPLICATION_SCHEMA` for the test if renamed |
| Object types | `Integration`, `Application` | `Application` is the usual CMDB term, which makes adoption easier |
| Object label / lookup key | `Name` (unique) | Integration Name = the SOT name = the Boomi process name. Alerts carry it as `ci_name`. |
| Attribute naming | SOT column names verbatim (`Legacy (Y/N)`, `Git hub location`…) | Rename to house style later; only the import mapping changes |
| Resolver group | `Resolver Group`, type **Group** | Values must be existing Jira groups. Placeholders: `Current Students`, `Data Squad`. |
| Lifecycle | `Status`, Assets built-in **Status** type: `Production`, `Production - To Retire`, `Development`, `Retired` | The SOT's values; map them to the organisation's lifecycle later |
| Reference types | `Reads from` (Source System), `Writes to` (Target System) | Give *Writes to* a distinct colour; it shows the blast radius in the graph view |
| Name variants | `Aliases` on Application | Used only by the initial load to resolve SOT spellings (`EDW (SVC_IDM)` → EDW) |
| Multi-value text | ` \| ` separator (Connectors) | Kept as text, verbatim from the SOT |

## The schemas

**Applications** (proposed): `Name`, `Description`, `Aliases`, `Resolver Group`, `Status`, `Hosting`.

**Connector** (optional, in the Integrations schema): `Name`, `Description`, `Connects To` (→ Application),
`Platform`, `Resolver Group`, `Status`. See [Optional connector layer](#optional-connector-layer).

**Integrations**:
- **SOT columns, verbatim:** Status, Platform, Method, Process Type, Integration Pattern, Schedule,
  Frequency, Volume per execution (MB), Volume per week (MB), Domain, Subdomain, Source Hosting,
  Target Hosting, Security, Legacy (Y/N), Log Source, Info Source, Source Confidence, Connectors,
  Git hub location, Notes.
- **References:**
  - **Source System** and **Target System**, both pointing to Applications;
  - optional **Connector CIs** (several allowed) pointing to Connector objects. The SOT's `Connectors`
    text keeps the full list either way.
- **Additions:** Name, Description, Resolver Group.

Left out of the Integration schema:
- the SOT's unnamed column and its `Volume` column, which are empty;
- Source/Target System as text, because they became references.

## Optional connector layer

Some integrations (Boomi) will have connectors that send their own heartbeat; others (SSIS,
DataStage, legacy) won't. So connectors are an **optional add-on**:

- Create Connector objects only for connectors that are monitored. List them in
  `initial-load/scope-connectors.csv`. Delete that file, and no Connector objects or links are made;
  everything else works unchanged.
- An integration's **Connector CIs** links only the connectors that exist as CIs. It can be empty, or
  partial: the general DXMULTI-0001 flow lists 3 connectors in the SOT text but links 2.
- A connector heartbeat alert carries `ci_name` = the Connector's Name, like every other CI.

Correlation adds the connector layer **only when it applies**. Root-CI precedence:

| What is failing | Root CI | Ticket goes to |
|---|---|---|
| Application health (plus its connectors and integrations) | **Application** | Application's Resolver Group |
| Connector heartbeat + the integrations linked to it, connected application healthy | **Connector** | Connector's Resolver Group |
| One integration only | **Integration** | Integration's Resolver Group |

For an integration with no Connector CIs, the middle row never applies; it's simply skipped.

## Initial load (once)

```bash
python3 scripts/sot-initial-load.py "Integrations SOT(Integrations).csv"
python3 scripts/preview-assets-graph.py
```

What the load script does:
- copies each scoped integration's SOT row verbatim;
- if `scope-connectors.csv` exists, creates those Connector objects and links each integration to the
  ones its SOT `Connectors` text mentions. It fails if a scoped connector isn't used by any scoped
  integration;
- resolves Source/Target System through Aliases to a canonical application, failing if any name can't be resolved;
- blanks non-numeric volumes (`No Data`);
- adds Resolver Group and Description from the scope file.

The validator then checks columns, required and unique values, Select/Status options, numeric values,
and the cross-schema references. It accepts a missing connector file. Currently: 8 objects (2
applications, 2 connectors, 4 integrations), 18 references; without connectors, 6 objects and 8 references.

After importing, manage the objects in Assets. Re-running the load with identifier = `Name` would
overwrite edits made in Assets, so only re-run it to add **new** integrations, with a scope file
listing just those.

## Create the schemas and import (UI)

1. **Create schema `Applications`**:
   - object type `Application`;
   - attributes from `applications-schema/00-object-type-attributes.csv`, with Resolver Group as
     type **Group** and Status as type **Status** (create the four statuses).
2. **Create schema `Integrations`**:
   - object type `Connector`, only if you use connectors;
   - object type `Integration`;
   - attributes from `integrations-schema/00-object-type-attributes.csv`;
   - for **Source System** and **Target System**, choose type *Object*, schema `Applications`, type
     `Application`, and the reference types `Reads from` and `Writes to`;
   - for Connector **Connects To**, the same, with reference type `Connects to`;
   - for Integration **Connector CIs**, type *Object* → `Connector`, reference type `Uses`, unlimited values.
3. **Make sure the resolver groups exist in Jira** (`Current Students`, `Data Squad`, or your real
   group names). Group values that don't match an existing group are not imported.
4. **Import in this order:** `applications-schema/01-applications.csv`, then
   `integrations-schema/01-connectors.csv` (if present), then `integrations-schema/02-integrations.csv`.
   - CSV import, identifier = `Name`.
   - Reference columns hold the referenced object's Name: map them with `Name = ${Source System}` /
     `Name = ${Target System}` against `Application`, and `Name = ${Connector CIs}` against `Connector`.
   - `Connector CIs` holds several values separated by ` | `. Set the import's multi-value separator
     to `|`, and check it on one integration first.

## Test the lookups

Use a service account with access to both schemas, the same account Keep will use. Put the values in
`.env` (gitignored):

```bash
JIRA_SITE=https://<your-site>.atlassian.net
JIRA_EMAIL=svc-observability@example.com
JIRA_API_TOKEN=<token>
```

```bash
python3 scripts/test-assets-lookup.py
```

It checks that:
1. each object is found by Name in its schema, with the expected Resolver Group, Status and Source/Target System;
2. the blast radius is complete (all 4 flows write to EDW);
3. every `ci_name` in `observability/prometheus/prometheus.yml` resolves to a CI;
4. the alerts of an EDW outage collapse to root CI **EDW**, Resolver Group **Data Squad**;
5. when connectors are in use: each Connector is found, its Connects To is right, each integration's
   Connector CIs match, and each connector routes to a Resolver Group.

The script is read-only. Exit codes: `0` passed, `1` a check failed, `2` configuration or connection
error. For Jira Data Center, set `JIRA_DEPLOYMENT=datacenter` and `JIRA_PAT`.

## How correlation uses this

| Alert (`ci_name`) | Found in | Root CI rule | Root CI → ticket goes to |
|---|---|---|---|
| `EDW` (`up{job="edw"} == 0`) | Applications | an Application is its own root | EDW → **Data Squad** |
| `ETL - DXMULTI-0001 : … - Student` (failed run) | Integrations | target-side error → its **Target System** | EDW → **Data Squad** |
| the Curriculum and general flows | Integrations | same | EDW → **Data Squad** |

All four alerts become one incident, routed to Data Squad. The integrations' own Resolver Group
(Current Students) is informed. The blast radius
(`objectType = Integration AND "Target System" = "EDW"`) also lists **Study Plan**, which isn't
monitored in the POC but is affected. If the source fails instead (a `source_*` error), the root is
Technology One (SM) → Current Students.

## Adopting the CMDB's application schema

**If the CMDB team already has an application object type:**
1. Find their objects for Technology One (SM) and EDW. Note any name differences, and add them to
   `Aliases` if you need them for re-mapping.
2. In the Integrations schema, change **Source System** and **Target System** to reference *their*
   object type. The attribute names and the Integration objects stay the same.
3. Re-map each integration's references to their objects, either through a CSV re-import of only
   `Name, Source System, Target System` with their names, or by hand for a few objects.
4. Use their resolver/support group for applications, and retire the proposed Applications schema.

**If they don't have one:** hand over the Applications schema as a starting point. They rename the
placeholders to their standard; your Integrations schema keeps working.

## Deliberately left out (add only if needed)

- **The 15 `RPS - DXMULTI-0001 : AppEvent - <Entity>` modules.**
  - These are on-demand Boomi modules, one per data entity, called by the ETL flows. They have no
    source or target system in the SOT.
  - Their failures surface as a failed parent flow. Linking them to their parent would need a field
    that isn't in the SOT.
- **Platforms, contacts, credentials, vendor details.**
  - People and on-call come from the Resolver Group, plus JSM on-call schedules for that group.
  - Unmonitored connectors stay as SOT text.
- **Open data points:**
  - the subdomain naming (the SOT says *Student Enrolment*);
  - the drafted Study Plan description;
  - the real Jira group names behind the placeholders;
  - the connectors' resolver group (placeholder `Integration & Platforms`);
  - what sends connector heartbeats in production.
