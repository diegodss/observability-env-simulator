#!/usr/bin/env python3
"""Validate the Jira Assets CSVs offline and print the object graph they produce.

Checks every object CSV in docs/jira-assets against 00-object-type-attributes.csv
the way the import would see it: unknown columns, missing required values,
duplicate unique values, select values outside their options, Integer/Float/
Date/Time values that don't parse, references to objects that don't exist (or
are imported later), and Monitoring Job keys that collide across object types
(Keep looks them up schema-wide). Multi-value references are `|`-separated.

    python3 scripts/preview-assets-graph.py          # checks + text tree
    python3 scripts/preview-assets-graph.py --json   # nodes/edges as JSON

Exit code 0 when the CSVs are consistent, 1 otherwise. No network access.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

CSV_DIR = Path(__file__).resolve().parents[1] / "docs" / "jira-assets"
IMPORT_FILES = {  # import order: every referenced type comes first
    "Team": "01-teams.csv",
    "Business Service": "02-business-services.csv",
    "Integration Platform": "03-integration-platforms.csv",
    "Application": "04-applications.csv",
    "Connector": "05-connectors.csv",
    "Integration": "06-integrations.csv",
    "Integration Component": "07-integration-components.csv",
}
MULTI_SEPARATOR = "|"


def read(name: str) -> list[dict]:
    with open(CSV_DIR / name, newline="") as handle:
        return list(csv.DictReader(handle))


def split(value: str, definition: dict) -> list[str]:
    if definition["Cardinality"] == "Multiple":
        return [v.strip() for v in value.split(MULTI_SEPARATOR) if v.strip()]
    return [value] if value else []


def type_error(value: str, attribute_type: str) -> str | None:
    try:
        if attribute_type == "Integer":
            int(value)
        elif attribute_type == "Float":
            float(value)
        elif attribute_type == "Date":
            date.fromisoformat(value)
        elif attribute_type.startswith("Time"):
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError
        elif attribute_type == "URL":
            if not re.match(r"https?://", value):
                raise ValueError
        return None
    except ValueError:
        return f"'{value}' is not a valid {attribute_type}"


def main() -> int:
    spec = defaultdict(dict)
    for row in read("00-object-type-attributes.csv"):
        spec[row["Object Type"]][row["Attribute"]] = row

    problems: list[str] = []
    objects: dict[str, dict[str, dict]] = {}   # type -> name -> row
    order = list(IMPORT_FILES)
    for object_type in spec:
        if object_type not in IMPORT_FILES:
            problems.append(f"spec defines '{object_type}' but no import file is listed for it")

    for object_type, filename in IMPORT_FILES.items():
        rows = read(filename)
        attributes = spec[object_type]
        objects[object_type] = {}
        columns = list(rows[0].keys()) if rows else []
        for column in columns:
            if column not in attributes:
                problems.append(f"{filename}: column '{column}' is not an attribute of {object_type}")
        for attribute, definition in attributes.items():
            in_csv = attribute in columns
            values = [r.get(attribute, "") for r in rows]
            if definition["Required"] == "Yes":
                if in_csv and not all(values):
                    problems.append(f"{filename}: required '{attribute}' is empty in some rows")
                if not in_csv and definition["Attribute Type"] != "User":
                    problems.append(f"{filename}: required '{attribute}' has no column")
            if definition["Unique"] == "Yes":
                dupes = sorted({v for v in values if v and values.count(v) > 1})
                if dupes:
                    problems.append(f"{filename}: '{attribute}' must be unique, duplicates: {dupes}")
            options = re.match(r"Select \((.*)\)", definition["Attribute Type"])
            for row, value in zip(rows, values):
                if not value:
                    continue
                if options and value not in options.group(1).split("|"):
                    problems.append(f"{filename} '{row['Name']}': '{attribute}' value '{value}' not in {options.group(1)}")
                error = type_error(value, definition["Attribute Type"])
                if error:
                    problems.append(f"{filename} '{row['Name']}': '{attribute}' {error}")
        for row in rows:
            objects[object_type][row["Name"]] = row

    jobs = defaultdict(list)
    for object_type, rows in objects.items():
        for name, row in rows.items():
            if row.get("Monitoring Job"):
                jobs[row["Monitoring Job"]].append(f"{object_type} '{name}'")
    for job, owners in jobs.items():
        if len(owners) > 1:
            problems.append(f"Monitoring Job '{job}' is used by {len(owners)} objects: {owners}")

    edges = []
    for object_type in IMPORT_FILES:
        for name, row in objects[object_type].items():
            for attribute, definition in spec[object_type].items():
                if definition["Attribute Type"] != "Object":
                    continue
                target_type = definition["Referenced Object Type"]
                for target in split(row.get(attribute, ""), definition):
                    if target not in objects.get(target_type, {}):
                        problems.append(f"{object_type} '{name}': {attribute} -> '{target}' is not a {target_type}")
                    elif order.index(target_type) > order.index(object_type):
                        problems.append(f"{object_type} '{name}': {attribute} -> {target_type} is imported later")
                    edges.append({"from": name, "from_type": object_type, "to": target, "to_type": target_type,
                                  "attribute": attribute, "reference": definition["Reference Type"]})

    nodes = [{"name": n, "type": t, "monitoring_job": r.get("Monitoring Job", "")}
             for t, rows in objects.items() for n, r in rows.items()]

    if "--json" in sys.argv:
        print(json.dumps({"nodes": nodes, "edges": edges, "problems": problems}, indent=2))
        return 1 if problems else 0

    counts = ", ".join(f"{len(objects[t])} {t}" for t in IMPORT_FILES)
    print(f"{len(nodes)} objects ({counts}), {len(edges)} references\n")
    for object_type in IMPORT_FILES:
        for name in objects[object_type]:
            job = objects[object_type][name].get("Monitoring Job")
            print(f"{object_type}: {name}" + (f"  [Monitoring Job = {job}]" if job else ""))
            for edge in (e for e in edges if e["from"] == name and e["from_type"] == object_type):
                print(f"    --{edge['reference']}--> {edge['to']}  ({edge['attribute']})")
            inbound = defaultdict(list)
            for edge in (e for e in edges if e["to"] == name and e["to_type"] == object_type):
                inbound[(edge["reference"], edge["from_type"])].append(edge["from"])
            for (reference, from_type), sources in inbound.items():
                print(f"    <--{reference}-- {len(sources)} {from_type}: {', '.join(sources) if len(sources) <= 3 else ''}".rstrip(": "))
    print()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("CSVs are consistent" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
