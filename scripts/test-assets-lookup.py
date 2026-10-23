#!/usr/bin/env python3
"""Check that Jira Assets answers the lookups the alert correlation relies on.

Expected objects are read from docs/jira-assets/04-applications.csv, 05-connectors.csv and
06-integrations.csv, so the test always matches what you imported.

For every Application and Integration it checks that:
  1. `"Monitoring Job" = <job>` returns exactly one object of the right type,
  2. owner, integration ID/key, source/target, platform and connectors match the CSV,
  3. the blast-radius queries (by Target Application and by shared Connector) are complete,
  4. alerts from job=lms + the syncs that write to it all resolve to the same root CI.

Configuration (environment variables, or a .env file in the repo root):
  JIRA_SITE           https://<your-site>.atlassian.net
  JIRA_EMAIL          account email (Cloud)
  JIRA_API_TOKEN      API token (Cloud) - https://id.atlassian.com/manage-profile/security/api-tokens
  ASSETS_SCHEMA       object schema name (default "Observability POC")
  ASSETS_WORKSPACE_ID optional; discovered from JIRA_SITE when omitted (Cloud)
  JIRA_DEPLOYMENT     cloud (default) or datacenter
  JIRA_PAT            personal access token (Data Center)
  ASSETS_API_BASE     optional override of the Assets REST base URL

Exit code 0 when every check passes, 1 otherwise. Read-only: it never writes to Assets.
"""

import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "docs" / "jira-assets"


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_csv(name: str) -> list[dict]:
    with open(CSV_DIR / name, newline="") as handle:
        return list(csv.DictReader(handle))


class Assets:
    def __init__(self) -> None:
        self.deployment = os.getenv("JIRA_DEPLOYMENT", "cloud").lower()
        self.site = os.getenv("JIRA_SITE", "").rstrip("/")
        if self.deployment == "datacenter":
            token = require("JIRA_PAT")
            self.headers = {"Authorization": f"Bearer {token}"}
            self.base = os.getenv("ASSETS_API_BASE") or f"{require('JIRA_SITE').rstrip('/')}/rest/assets/1.0"
        else:
            raw = f"{require('JIRA_EMAIL')}:{require('JIRA_API_TOKEN')}".encode()
            self.headers = {"Authorization": "Basic " + base64.b64encode(raw).decode()}
            self.base = os.getenv("ASSETS_API_BASE") or self._cloud_base()
        self.headers["Accept"] = "application/json"
        self._attribute_names: dict[str, dict[str, str]] = {}

    def _cloud_base(self) -> str:
        workspace = os.getenv("ASSETS_WORKSPACE_ID")
        if not workspace:
            data = self._request("GET", f"{require('JIRA_SITE').rstrip('/')}/rest/servicedeskapi/assets/workspace")
            values = data.get("values", [])
            if not values:
                fail_fast("No Assets workspace found for this site (is Jira Service Management with Assets enabled?)")
            workspace = values[0]["workspaceId"]
        return f"https://api.atlassian.com/jsm/assets/workspace/{workspace}/v1"

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            hint = {401: "check credentials", 403: "the account needs Assets access to this schema",
                    404: "check JIRA_SITE / workspace / ASSETS_API_BASE"}.get(exc.code, "")
            fail_fast(f"HTTP {exc.code} for {method} {url} {('(' + hint + ')') if hint else ''}\n{detail}")
        except urllib.error.URLError as exc:
            fail_fast(f"Cannot reach {url}: {exc.reason}")

    def aql(self, query: str) -> list[dict]:
        """Run an AQL query and return objects as {label, type, attrs{name: [values]}}."""
        if self.deployment == "datacenter":
            params = urllib.parse.urlencode({"qlQuery": query, "includeAttributes": "true", "resultPerPage": 100})
            data = self._request("GET", f"{self.base}/aql/objects?{params}")
        else:
            data = self._request("POST", f"{self.base}/object/aql?startAt=0&maxResults=100&includeAttributes=true",
                                 {"qlQuery": query})
        names = {str(a["id"]): a["name"] for a in data.get("objectTypeAttributes", []) if "id" in a}
        objects = []
        for entry in data.get("values", data.get("objectEntries", [])):
            object_type = entry.get("objectType", {})
            attrs: dict[str, list[str]] = {}
            for attr in entry.get("attributes", []):
                attr_id = str(attr.get("objectTypeAttributeId"))
                name = (attr.get("objectTypeAttribute") or {}).get("name") or names.get(attr_id) \
                    or self._attribute_name(str(object_type.get("id")), attr_id)
                attrs[name] = [value_of(v) for v in attr.get("objectAttributeValues", [])]
            objects.append({"label": entry.get("label"), "type": object_type.get("name"), "attrs": attrs})
        return objects

    def _attribute_name(self, object_type_id: str, attr_id: str) -> str:
        if object_type_id not in self._attribute_names:
            listing = self._request("GET", f"{self.base}/objecttype/{object_type_id}/attributes")
            items = listing if isinstance(listing, list) else listing.get("values", [])
            self._attribute_names[object_type_id] = {str(a["id"]): a["name"] for a in items}
        return self._attribute_names[object_type_id].get(attr_id, f"attribute#{attr_id}")


def value_of(value: dict) -> str:
    referenced = value.get("referencedObject")
    if referenced:
        return referenced.get("label") or referenced.get("name") or ""
    return str(value.get("displayValue") or value.get("value") or "")


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        fail_fast(f"Set {name} (see the header of {Path(__file__).name})")
    return value


def fail_fast(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.failures += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        return ok


def first(obj: dict, attribute: str) -> str:
    values = obj["attrs"].get(attribute, [])
    return values[0] if values else ""


def split(value: str) -> list[str]:
    return sorted(v.strip() for v in value.split("|") if v.strip())


def main() -> int:
    load_dotenv()
    schema = os.getenv("ASSETS_SCHEMA", "Observability POC")
    applications = read_csv("04-applications.csv")
    connectors = read_csv("05-connectors.csv")
    integrations = read_csv("06-integrations.csv")
    assets = Assets()
    report = Report()
    in_schema = f"objectSchema = {quote(schema)}"
    found: dict[str, dict] = {}

    print(f"Assets API: {assets.base}\nSchema: {schema}\n")
    print("1. Lookup by Monitoring Job (what Keep does for every alert)")
    for object_type, rows in (("Application", applications), ("Integration", integrations)):
        for row in rows:
            job = row["Monitoring Job"]
            if not job:
                print(f"  [SKIP] {object_type} '{row['Name']}' has no Monitoring Job (not monitored)")
                continue
            query = f'{in_schema} AND "Monitoring Job" = {quote(job)}'
            results = assets.aql(query)
            if not report.check(len(results) == 1, f"job={job}", f"{len(results)} objects for: {query}"
                                if len(results) != 1 else f"{results[0]['type']} '{results[0]['label']}'"):
                continue
            obj = found[job] = results[0]
            report.check(obj["type"] == object_type, f"job={job} object type", f"expected {object_type}, got {obj['type']}")
            report.check(obj["label"] == row["Name"], f"job={job} name", f"expected {row['Name']!r}, got {obj['label']!r}")
            expected = {"Owner Team": row["Owner Team"], "Business Service": row["Business Service"]}
            if object_type == "Integration":
                expected |= {k: row[k] for k in ("Integration ID", "Integration Key", "Source Application",
                                                 "Target Application", "Platform")}
            for attribute, value in expected.items():
                actual = first(obj, attribute)
                report.check(actual == value, f"job={job} {attribute}", f"expected {value!r}, got {actual!r}")
            if object_type == "Integration":
                actual = sorted(obj["attrs"].get("Connectors", []))
                report.check(actual == split(row["Connectors"]), f"job={job} Connectors", f"{actual}")

    print("\n2. Blast radius (integrations writing to each application)")
    for app in applications:
        expected = sorted(i["Name"] for i in integrations if i["Target Application"] == app["Name"])
        query = f'{in_schema} AND objectType = "Integration" AND "Target Application" = {quote(app["Name"])}'
        actual = sorted(o["label"] for o in assets.aql(query))
        report.check(actual == expected, f"Target Application = {app['Name']}", f"{len(actual)}: {actual}")

    print("\n   Blast radius per connector (integrations that share it)")
    for connector in connectors:
        expected = sorted(i["Name"] for i in integrations if connector["Name"] in split(i["Connectors"]))
        query = f'{in_schema} AND objectType = "Integration" AND "Connectors" = {quote(connector["Name"])}'
        actual = sorted(o["label"] for o in assets.aql(query))
        report.check(actual == expected, f"Connectors = {connector['Name']}", f"{len(actual)} integrations")

    target = next(a["Name"] for a in applications if a["Monitoring Job"] == "lms")
    print(f"\n3. Root-CI correlation for an outage of job=lms ({target})")
    outage_jobs = ["lms"] + [i["Monitoring Job"] for i in integrations
                             if i["Target Application"] == target and i["Monitoring Job"]]
    roots = {}
    for job in outage_jobs:
        obj = found.get(job)
        if obj is None:
            report.check(False, f"job={job}", "not found in step 1")
            continue
        roots[job] = first(obj, "Target Application") if obj["type"] == "Integration" else obj["label"]
        print(f"      alert job={job:<14} -> root CI {roots[job]}")
    if report.check(len(set(roots.values())) == 1 and len(roots) == len(outage_jobs),
                    f"{len(outage_jobs)} alerts collapse to one root CI", ", ".join(sorted(set(roots.values())))):
        root = next(iter(roots.values()))
        root_obj = next((o for o in found.values() if o["label"] == root and o["type"] == "Application"), None)
        owner = first(root_obj, "Owner Team") if root_obj else ""
        report.check(bool(owner), "root CI has an owner to route the ticket to", owner)

    print(f"\n{'All checks passed' if not report.failures else f'{report.failures} check(s) failed'}")
    return 0 if not report.failures else 1


if __name__ == "__main__":
    sys.exit(main())
