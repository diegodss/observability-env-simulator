#!/usr/bin/env python3
"""Validate the Jira Assets CSVs offline and print the object graph they produce.

Each schema folder under docs/jira-assets has a 00-object-type-attributes.csv
spec and numbered import files. Checks each import file against its spec the
way the import would see it: unknown columns, missing required values,
duplicate unique values, Select/Status values outside their options, Float/
Integer/Date/URL values that don't parse, and object references (including
across schemas) to objects that don't exist or are imported later. Optional
object types (Connector) may have no import file at all.

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

DOCS = Path(__file__).resolve().parents[1] / "docs" / "jira-assets"
SCHEMAS = [  # import order: referenced schemas and types come first
    ("Applications", "applications-schema", [("Application", "01-applications.csv")]),
    ("Integrations", "integrations-schema", [("Connector", "01-connectors.csv"), ("Integration", "02-integrations.csv")]),
]
OPTIONAL_TYPES = {"Connector"}  # an absent import file means "not used", not an error
MULTI_SEPARATOR = "|"


def read(path: Path) -> list[dict]:
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def type_error(value: str, attribute_type: str) -> str | None:
    try:
        if attribute_type == "Integer":
            int(value)
        elif attribute_type == "Float":
            float(value)
        elif attribute_type == "Date":
            date.fromisoformat(value)
        elif attribute_type == "URL" and not re.match(r"https?://", value):
            raise ValueError
        return None
    except ValueError:
        return f"'{value}' is not a valid {attribute_type}"


def main() -> int:
    problems: list[str] = []
    spec: dict[tuple, dict] = {}                 # (schema, type) -> attribute -> definition
    objects: dict[tuple, dict[str, dict]] = {}   # (schema, type) -> name -> row
    order = [(schema, t) for schema, _, types in SCHEMAS for t, _ in types]

    for schema, folder, types in SCHEMAS:
        definitions = defaultdict(dict)
        for row in read(DOCS / folder / "00-object-type-attributes.csv"):
            definitions[row["Object Type"]][row["Attribute"]] = row
        for object_type in definitions:
            if object_type not in dict(types):
                problems.append(f"{folder}: spec defines '{object_type}' but no import file is listed for it")
        for object_type, filename in types:
            key, label = (schema, object_type), f"{folder}/{filename}"
            spec[key] = attributes = definitions[object_type]
            if not (DOCS / folder / filename).exists():
                if object_type not in OPTIONAL_TYPES:
                    problems.append(f"{label}: import file is missing")
                objects[key] = {}
                continue
            rows = read(DOCS / folder / filename)
            objects[key] = {row["Name"]: row for row in rows}
            columns = list(rows[0].keys()) if rows else []
            for column in columns:
                if column not in attributes:
                    problems.append(f"{label}: column '{column}' is not an attribute of {object_type}")
            for attribute, definition in attributes.items():
                values = [r.get(attribute, "") for r in rows]
                if definition["Required"] == "Yes" and (attribute not in columns or not all(values)):
                    problems.append(f"{label}: required '{attribute}' is missing or empty")
                if definition["Unique"] == "Yes":
                    dupes = sorted({v for v in values if v and values.count(v) > 1})
                    if dupes:
                        problems.append(f"{label}: '{attribute}' must be unique, duplicates: {dupes}")
                options = re.match(r"(?:Select|Status) \((.*)\)", definition["Attribute Type"])
                for row, value in zip(rows, values):
                    if not value:
                        continue
                    if options and value not in options.group(1).split("|"):
                        problems.append(f"{label} '{row['Name']}': '{attribute}' value '{value}' not in {options.group(1)}")
                    error = type_error(value, definition["Attribute Type"])
                    if error:
                        problems.append(f"{label} '{row['Name']}': '{attribute}' {error}")

    edges = []
    for key in order:
        schema, object_type = key
        for name, row in objects[key].items():
            for attribute, definition in spec[key].items():
                if definition["Attribute Type"] != "Object" or not row.get(attribute):
                    continue
                target = (definition["Referenced Schema"] or schema, definition["Referenced Object Type"])
                values = row[attribute].split(MULTI_SEPARATOR) if definition["Cardinality"] == "Multiple" else [row[attribute]]
                for value in (v.strip() for v in values):
                    if target not in objects:
                        problems.append(f"{object_type} '{name}': {attribute} references unknown type {target[1]} in schema {target[0]}")
                    elif value not in objects[target]:
                        problems.append(f"{object_type} '{name}': {attribute} -> '{value}' is not a {target[1]} in {target[0]}")
                    elif order.index(target) > order.index(key):
                        problems.append(f"{object_type} '{name}': {attribute} -> {target[1]} is imported later")
                    edges.append({"from": name, "from_type": object_type, "to": value, "to_type": target[1],
                                  "to_schema": target[0], "attribute": attribute, "reference": definition["Reference Type"]})

    nodes = [{"name": n, "type": t, "schema": s, "resolver_group": r.get("Resolver Group", "")}
             for (s, t), rows in objects.items() for n, r in rows.items()]

    if "--json" in sys.argv:
        print(json.dumps({"nodes": nodes, "edges": edges, "problems": problems}, indent=2))
        return 1 if problems else 0

    counts = ", ".join(f"{len(objects[k])} {k[1]} ({k[0]})" for k in order)
    print(f"{len(nodes)} objects: {counts}; {len(edges)} references\n")
    for schema, object_type in order:
        for name, row in objects[(schema, object_type)].items():
            print(f"[{schema}] {object_type}: {name}  (Resolver Group = {row.get('Resolver Group') or '-'})")
            for edge in (e for e in edges if e["from"] == name and e["from_type"] == object_type):
                print(f"    --{edge['reference']}--> {edge['to']}  ({edge['attribute']}, {edge['to_schema']})")
            inbound = defaultdict(list)
            for edge in (e for e in edges if e["to"] == name and e["to_type"] == object_type):
                inbound[(edge["reference"], edge["from_type"])].append(edge["from"])
            for (reference, from_type), sources in inbound.items():
                print(f"    <--{reference}-- {len(sources)} {from_type}(s)")
    print()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("CSVs are consistent" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
