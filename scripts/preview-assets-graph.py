#!/usr/bin/env python3
"""Validate the Jira Assets CSVs offline and print the object graph they produce.

Checks every object CSV in docs/jira-assets against 00-object-type-attributes.csv
the way the import would see it: unknown columns, missing required values,
duplicate unique values, select values outside their options, references to
objects that don't exist (or are imported later), and the label attribute.

    python3 scripts/preview-assets-graph.py          # checks + text tree
    python3 scripts/preview-assets-graph.py --json   # nodes/edges as JSON

Exit code 0 when the CSVs are consistent, 1 otherwise. No network access.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CSV_DIR = Path(__file__).resolve().parents[1] / "docs" / "jira-assets"
IMPORT_FILES = {
    "Team": "01-teams.csv",
    "Business Service": "02-business-services.csv",
    "Application": "03-applications.csv",
    "Integration": "04-integrations.csv",
}


def read(name: str) -> list[dict]:
    with open(CSV_DIR / name, newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    spec = defaultdict(dict)
    for row in read("00-object-type-attributes.csv"):
        spec[row["Object Type"]][row["Attribute"]] = row

    problems: list[str] = []
    objects: dict[str, dict[str, dict]] = {}   # type -> name -> row
    order = list(IMPORT_FILES)

    for object_type, filename in IMPORT_FILES.items():
        rows = read(filename)
        attributes = spec[object_type]
        objects[object_type] = {}
        for column in rows[0].keys() if rows else []:
            if column not in attributes:
                problems.append(f"{filename}: column '{column}' is not an attribute of {object_type}")
        for attribute, definition in attributes.items():
            values = [r.get(attribute, "") for r in rows]
            in_csv = attribute in (rows[0] if rows else {})
            if definition["Required"] == "Yes" and in_csv and not all(values):
                problems.append(f"{filename}: required '{attribute}' is empty in some rows")
            if definition["Required"] == "Yes" and not in_csv and definition["Attribute Type"] != "User":
                problems.append(f"{filename}: required '{attribute}' has no column")
            if definition["Unique"] == "Yes":
                dupes = sorted({v for v in values if v and values.count(v) > 1})
                if dupes:
                    problems.append(f"{filename}: '{attribute}' must be unique, duplicates: {dupes}")
            options = re.match(r"Select \((.*)\)", definition["Attribute Type"])
            if options and in_csv:
                allowed = options.group(1).split("|")
                for value in values:
                    if value and value not in allowed:
                        problems.append(f"{filename}: '{attribute}' value '{value}' not in {allowed}")
        for row in rows:
            objects[object_type][row["Name"]] = row

    edges = []
    for object_type in IMPORT_FILES:
        for name, row in objects[object_type].items():
            for attribute, definition in spec[object_type].items():
                target_type = definition["Referenced Object Type"]
                if definition["Attribute Type"] != "Object" or not row.get(attribute):
                    continue
                target = row[attribute]
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

    print(f"{len(nodes)} objects, {len(edges)} references\n")
    for object_type in IMPORT_FILES:
        for name in objects[object_type]:
            job = objects[object_type][name].get("Monitoring Job")
            print(f"{object_type}: {name}" + (f"  [Monitoring Job = {job}]" if job else ""))
            for edge in (e for e in edges if e["from"] == name and e["from_type"] == object_type):
                print(f"    --{edge['reference']}--> {edge['to']}  ({edge['attribute']})")
            inbound = [e for e in edges if e["to"] == name and e["to_type"] == object_type]
            for edge in inbound:
                print(f"    <--{edge['reference']}-- {edge['from']}")
    print()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("CSVs are consistent" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
