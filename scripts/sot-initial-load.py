#!/usr/bin/env python3
"""One-off initial load: turn Integrations SOT rows into Jira Assets import CSVs.

The SOT is only the starting point. After the first import, Jira Assets is the
source of truth and this script is not run again for those objects.

    python3 scripts/sot-initial-load.py "Integrations SOT(Integrations).csv"

Inputs (docs/jira-assets/initial-load/):
  scope-integrations.csv  which SOT rows to load, plus Resolver Group and Description
  scope-applications.csv  canonical application names, Aliases (SOT spellings),
                          Resolver Group, Status and Description
  scope-connectors.csv    OPTIONAL: connectors that exist as CIs (e.g. send a
                          heartbeat), with Connects To, Resolver Group, Status,
                          Description. Without it, no Connector objects are made.

Outputs:
  docs/jira-assets/applications-schema/01-applications.csv
  docs/jira-assets/integrations-schema/01-connectors.csv   (only with scope-connectors.csv)
  docs/jira-assets/integrations-schema/02-integrations.csv

Fails (exit 1) if a scoped integration is missing from the SOT, if its
Source/Target System does not resolve to a scoped application name or alias, or
if a scoped connector is not used by any scoped integration in the SOT.
"""

import csv
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs" / "jira-assets"
SCOPE = DOCS / "initial-load"

# Integration attribute <- SOT column. Attribute names match the SOT verbatim.
SOT_COLUMNS = [
    "Status", "Platform", "Method", "Process Type", "Integration Pattern", "Schedule", "Frequency",
    "Volume per execution (MB)", "Volume per week (MB)", "Domain", "Subdomain", "Source Hosting",
    "Target Hosting", "Security", "Legacy (Y/N)", "Log Source", "Info Source", "Source Confidence",
    "Connectors", "Git hub location", "Notes",
]
NUMERIC = {"Volume per execution (MB)", "Volume per week (MB)"}


def read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def clean(column: str, value: str) -> str:
    value = value.strip()
    if column in NUMERIC and not re.fullmatch(r"\d+(\.\d+)?", value):
        return ""  # 'No Data', 'Not recorded' -> empty Float
    if column == "Connectors":
        return " | ".join(part.strip() for part in value.split("|") if part.strip())
    return value


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    sot = {row["Integration Name"].strip(): row for row in read(Path(sys.argv[1]))}
    applications = read(SCOPE / "scope-applications.csv")
    integrations = read(SCOPE / "scope-integrations.csv")
    connector_scope = SCOPE / "scope-connectors.csv"
    connectors = read(connector_scope) if connector_scope.exists() else []
    connector_names = {c["Name"] for c in connectors}
    connector_platforms: dict[str, set[str]] = {name: set() for name in connector_names}
    problems = []

    canonical = {}
    for app in applications:
        canonical[app["Name"]] = app["Name"]
        for alias in filter(None, (a.strip() for a in app["Aliases"].split("|"))):
            canonical[alias] = app["Name"]

    hosting: dict[str, set[str]] = {app["Name"]: set() for app in applications}
    integration_rows = []
    for scoped in integrations:
        name = scoped["Integration Name"]
        row = sot.get(name)
        if row is None:
            problems.append(f"'{name}' is not in the SOT")
            continue
        systems = {}
        for column in ("Source System", "Target System"):
            value = row[column].strip()
            systems[column] = canonical.get(value, "")
            if not systems[column]:
                problems.append(f"'{name}': {column} '{value}' is not a scoped application or alias")
        for column, app in (("Source Hosting", systems["Source System"]), ("Target Hosting", systems["Target System"])):
            if app and row[column].strip():
                hosting[app].add(row[column].strip())
        used = [c for c in clean("Connectors", row["Connectors"]).split(" | ") if c in connector_names]
        for connector in used:
            connector_platforms[connector].add(row["Platform"].strip())
        integration_rows.append([name, scoped["Description"], systems["Source System"], systems["Target System"],
                                 scoped["Resolver Group"], *(clean(c, row[c]) for c in SOT_COLUMNS), " | ".join(used)])

    app_names = {app["Name"] for app in applications}
    connector_rows = []
    for connector in connectors:
        name = connector["Name"]
        if not connector_platforms[name]:
            problems.append(f"connector '{name}' is not used by any scoped integration in the SOT")
        if connector["Connects To"] and connector["Connects To"] not in app_names:
            problems.append(f"connector '{name}': Connects To '{connector['Connects To']}' is not a scoped application")
        platform = next(iter(connector_platforms[name])) if len(connector_platforms[name]) == 1 else ""
        connector_rows.append([name, connector["Description"], connector["Connects To"], platform,
                               connector["Resolver Group"], connector["Status"]])

    application_rows = [[app["Name"], app["Description"], app["Aliases"], app["Resolver Group"], app["Status"],
                         " | ".join(sorted(hosting[app["Name"]]))] for app in applications]

    if problems:
        for problem in problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
        return 1
    write(DOCS / "applications-schema" / "01-applications.csv",
          ["Name", "Description", "Aliases", "Resolver Group", "Status", "Hosting"], application_rows)
    connector_file = DOCS / "integrations-schema" / "01-connectors.csv"
    if connector_rows:
        write(connector_file, ["Name", "Description", "Connects To", "Platform", "Resolver Group", "Status"], connector_rows)
    elif connector_file.exists():
        connector_file.unlink()  # connectors are optional: no scope file, no Connector objects
    write(DOCS / "integrations-schema" / "02-integrations.csv",
          ["Name", "Description", "Source System", "Target System", "Resolver Group", *SOT_COLUMNS, "Connector CIs"],
          integration_rows)
    print(f"Wrote {len(application_rows)} applications, {len(connector_rows)} connectors and "
          f"{len(integration_rows)} integrations from {len(sot)} SOT rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
