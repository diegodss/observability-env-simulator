# Jira Assets model for alert enrichment

Keep enriches each alert by looking up its Prometheus `job` label in Jira Assets, then follows
references to find the root CI and its owner. See the
[correlation walkthrough](#how-correlation-uses-this) at the end.

| File | Purpose |
|---|---|
| `00-object-type-attributes.csv` | Specification of the 4 object types and their attributes. Use it as a checklist when creating them in the UI. It is not an import file. |
| `01-teams.csv` | Current Students, Data Squad |
| `02-business-services.csv` | Student Administration / Core Data (domain: Current Students) |
| `03-applications.csv` | Technology One (SM), EDW |
| `04-integrations.csv` | The three ETL - DXMULTI-0001 AppEvents flows (Student, Curriculum, General) |
| [`../../scripts/test-assets-lookup.py`](../../scripts/test-assets-lookup.py) | Read-only check that the lookups Keep needs return the right answers |

Team emails and assignment groups are placeholders. Replace them with real values before importing.

### Names vs. monitoring keys

Object names are the real system names. **Monitoring Job** stays equal to the Prometheus `job`
label that the POC stack emits, because that is what Keep looks up:

| Assets object | Monitoring Job | POC service |
|---|---|---|
| Technology One (SM) | `sis` | `sis` (mock source) |
| EDW | `lms` | `lms` (mock target) |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Student | `sync-students` | `sync-students` |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW - Curriculum | `sync-courses` | `sync-courses` |
| ETL - DXMULTI-0001 : AppEvents from SMS to EDW (General) | `sync-staff` | `sync-staff` |

The POC's staff sync stands in for the General flow, since the mock has no organisational-unit
data. When the alerts come from the real systems, set Monitoring Job to the real `job` labels.

## 1. Create the schema and object types (UI)

CSV import loads *objects*. Object types and attributes are defined in the schema first:

1. **Assets → Create schema →** empty schema named **`Observability POC`**. If you use a different
   name, set `ASSETS_SCHEMA` for the test script.
2. **Schema settings → Reference types:** make sure `Owned by`, `Part of`, `Reads from` and `Writes to` exist.
   Add any that are missing; the default ones like `Depends on` are fine too.
3. Create the object types **in this order**, because an object reference needs its target type to
   exist first: **Team → Business Service → Application → Integration**.
4. Add the attributes listed in `00-object-type-attributes.csv` to each type:
   - `Name` already exists and is the label attribute.
   - **Monitoring Job**: Text, and tick **Unique** in its configuration. Every lookup depends on it.
   - Object attributes: pick the referenced object type and reference type from the spec.
   - Select attributes: add the options listed (`Tier 1|Tier 2|Tier 3`, `POC|Test|Production`).

## 2. Import the objects (CSV import)

**Schema → Import → Create import → CSV import.** Create one import mapping per file and run them in
file order (01 → 04), so referenced objects exist before they are referenced.

- Delimiter `,`, encoding UTF-8, first row is the header.
- Map each column to the attribute with the same name.
- Set **identifier = `Name`** for every mapping, so re-running an import updates objects instead
  of duplicating them.
- **Reference columns** (`Owner Team`, `Business Service`, `Source Application`,
  `Target Application`): the CSV holds the referenced object's **Name**. In the column mapping,
  set the object match to `Name = ${<column>}` (AQL placeholder). For example, `Owner Team` maps with
  `Name = ${Owner Team}` against object type Team.

With 9 objects, creating them by hand in the UI is a reasonable alternative. The CSVs still serve as
the record of what should exist, and the test script reads them.

## 3. Test the lookups

Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens. Use a service
account with Assets access to the schema, the same account Keep will use. Then add the values to `.env`
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

The script only reads, never writes. The Assets workspace ID is discovered automatically, or you can
set `ASSETS_WORKSPACE_ID`. For Jira Data Center, set `JIRA_DEPLOYMENT=datacenter` and `JIRA_PAT`; older
Insight versions may need `ASSETS_API_BASE=https://<host>/rest/insight/1.0`. Exit codes: `0` all checks
passed, `1` a check failed, `2` configuration, auth or connectivity error.

What it checks, using the CSVs as the expected state:

1. `objectSchema = "Observability POC" AND "Monitoring Job" = "<job>"` returns exactly one object
   for each of `sis`, `lms`, `sync-students`, `sync-staff` and `sync-courses`, with the expected type,
   name, owner, business service, integration key and source/target references.
2. Blast radius: `objectType = "Integration" AND "Target Application" = "EDW"` returns all three ETL flows.
3. Correlation: alerts from `lms` plus the three sync jobs all resolve to root CI **EDW**, and that CI
   has an owner to route the ticket to.

Expected tail of the output:

```
3. Root-CI correlation for an outage of job=lms (EDW)
      alert job=lms            -> root CI EDW
      alert job=sync-students  -> root CI EDW
      alert job=sync-courses   -> root CI EDW
      alert job=sync-staff     -> root CI EDW
  [PASS] 4 alerts collapse to one root CI - EDW
  [PASS] root CI has an owner to route the ticket to - Data Squad

All checks passed
```

## How correlation uses this

| Alert | Keep looks up | Root CI rule | Root CI |
|---|---|---|---|
| `up{job="lms"} == 0` | `"Monitoring Job" = "lms"` → Application EDW | an Application is its own root | EDW |
| `integration_last_run_success == 0`, `job="sync-students"` | → AppEvents - Student | an Integration's root is its **Target Application** | EDW |
| the same for `sync-courses` and `sync-staff` | → AppEvents - Curriculum, AppEvents (General) | Target Application | EDW |

Keep correlates on the enriched root CI. All four alerts become one incident, and the ticket goes to
the root CI's **Owner Team** (Data Squad, owner of EDW). It does not go to Current Students, who own the
ETL flows but only see the symptoms. The ticket can list the impacted ETL flows from the
blast-radius query. If Technology One (SM) is the system that fails, the root CI rule from the
`failed_system` label routes the ticket to Current Students instead.

The Assets API identifies attributes by numeric ID in its responses. The script resolves the
names, either from the response or from `GET /objecttype/{id}/attributes`. Keep's HTTP step needs the
same mapping, or a thin adapter that returns flat JSON (`owner`, `root_ci`, `runbook_url`, …).
