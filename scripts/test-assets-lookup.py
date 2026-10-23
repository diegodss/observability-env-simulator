#!/usr/bin/env python3
"""Check that Jira Assets answers the lookups the alert correlation relies on.

Alerts carry a `ci_name` label with the exact Assets object Name of the CI
(see observability/prometheus/prometheus.yml). Expected objects are read from
docs/jira-assets/applications-schema and integrations-schema, so the test
always matches what you imported.

It checks that:
  1. every Application and Integration is found by Name in its schema, exactly once,
     with the expected Resolver Group, Status and (for integrations) Source/Target System,
  2. the blast-radius query (integrations whose Target System is X) is complete,
  3. every ci_name in prometheus.yml resolves to a CI, and the alerts of an outage
     of job=edw all resolve to the same root CI, whose Resolver Group gets the ticket,
  4. if Connector objects are in use (integrations-schema/01-connectors.csv exists):
     each connector is found, linked to the right integrations, and can act as root CI.

Configuration (environment variables, or a .env file in the repo root):
  JIRA_SITE                  https://<your-site>.atlassian.net
  JIRA_EMAIL                 account email (Cloud)
  JIRA_API_TOKEN             API token (Cloud) - https://id.atlassian.com/manage-profile/security/api-tokens
  ASSETS_APPLICATION_SCHEMA  schema holding Application objects (default "Applications")
  ASSETS_INTEGRATION_SCHEMA  schema holding Integration objects (default "Integrations")
  ASSETS_WORKSPACE_ID        optional; discovered from JIRA_SITE when omitted (Cloud)
  JIRA_DEPLOYMENT            cloud (default) or datacenter
  JIRA_PAT                   personal access token (Data Center)
  ASSETS_API_BASE            optional override of the Assets REST base URL

Exit code 0 when every check passes, 1 otherwise. Read-only: it never writes to Assets.
"""

import base64
import csv
import json
import os
import re
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
    for key, field in (("referencedObject", "label"), ("group", "name"), ("user", "displayName"), ("status", "name")):
        nested = value.get(key)
        if isinstance(nested, dict) and nested.get(field):
            return nested[field]
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


def ci_names() -> dict[str, str]:
    """job -> ci_name, from the Prometheus scrape config the alerts come from."""
    text = (ROOT / "observability" / "prometheus" / "prometheus.yml").read_text()
    return dict(re.findall(r'job_name:\s*(\S+)[\s\S]*?ci_name:\s*"([^"]+)"', text))


def main() -> int:
    load_dotenv()
    integration_schema = os.getenv("ASSETS_INTEGRATION_SCHEMA", "Integrations")
    schemas = {"Application": os.getenv("ASSETS_APPLICATION_SCHEMA", "Applications"),
               "Connector": integration_schema, "Integration": integration_schema}
    expected = {"Application": read_csv("applications-schema/01-applications.csv")}
    if (CSV_DIR / "integrations-schema" / "01-connectors.csv").exists():  # connectors are optional
        expected["Connector"] = read_csv("integrations-schema/01-connectors.csv")
    expected["Integration"] = read_csv("integrations-schema/02-integrations.csv")
    assets = Assets()
    report = Report()
    found: dict[str, dict] = {}

    def scope(object_type: str) -> str:
        return f"objectSchema = {quote(schemas[object_type])} AND objectType = {quote(object_type)}"

    print(f"Assets API: {assets.base}\nSchemas: {schemas}\n")
    print("1. Lookup by Name (what Keep does with each alert's ci_name)")
    for object_type, rows in expected.items():
        for row in rows:
            name = row["Name"]
            results = assets.aql(f"{scope(object_type)} AND Name = {quote(name)}")
            if not report.check(len(results) == 1, f"{object_type} '{name}'", f"{len(results)} objects found"):
                continue
            obj = found[name] = results[0]
            checks = ["Resolver Group", "Status"] + {"Integration": ["Source System", "Target System"],
                                                      "Connector": ["Connects To"]}.get(object_type, [])
            for attribute in checks:
                actual = first(obj, attribute)
                report.check(actual == row[attribute], f"    {attribute}", f"expected {row[attribute]!r}, got {actual!r}")
            if object_type == "Integration" and "Connector" in expected:
                got, want = sorted(obj["attrs"].get("Connector CIs", [])), split(row["Connector CIs"])
                report.check(got == want, "    Connector CIs", f"{len(got)} linked")

    print("\n2. Blast radius (integrations writing to each application)")
    for app in expected["Application"]:
        want = sorted(i["Name"] for i in expected["Integration"] if i["Target System"] == app["Name"])
        got = sorted(o["label"] for o in assets.aql(f'{scope("Integration")} AND "Target System" = {quote(app["Name"])}'))
        report.check(got == want, f"Target System = {app['Name']}", f"{len(got)} integrations")

    for connector in expected.get("Connector", []):
        want = sorted(i["Name"] for i in expected["Integration"] if connector["Name"] in split(i["Connector CIs"]))
        got = sorted(o["label"] for o in assets.aql(f'{scope("Integration")} AND "Connector CIs" = {quote(connector["Name"])}'))
        report.check(got == want, f"Connector CIs = {connector['Name']}", f"{len(got)} integrations")

    print("\n3. Alert ci_name -> root CI -> resolver group")
    labels = ci_names()
    for job, name in labels.items():
        report.check(name in found, f"job={job} ci_name", f"'{name}'" + ("" if name in found else " not found in Assets"))
    outage = [job for job, name in labels.items()
              if job == "edw" or (name in found and first(found[name], "Target System") == labels.get("edw"))]
    roots = {}
    for job in outage:
        obj = found.get(labels[job])
        if obj:
            roots[job] = first(obj, "Target System") if obj["type"] == "Integration" else obj["label"]
            print(f"      alert job={job:<22} -> root CI {roots[job]}")
    if report.check(len(roots) == len(outage) and len(set(roots.values())) == 1,
                    f"{len(outage)} alerts of an outage of job=edw collapse to one root CI", ", ".join(sorted(set(roots.values())))):
        root = found.get(next(iter(roots.values())))
        group = first(root, "Resolver Group") if root else ""
        report.check(bool(group), "root CI has a Resolver Group to route the ticket to", group)

    if expected.get("Connector"):
        print("\n4. Connector as root CI (heartbeat failing, connected application healthy)")
        for connector in expected["Connector"]:
            obj = found.get(connector["Name"])
            if not obj:
                continue
            users = [i["Name"] for i in expected["Integration"] if connector["Name"] in split(i["Connector CIs"])]
            print(f"      {connector['Name']}: {len(users)} integrations grouped, connects to {first(obj, 'Connects To') or '-'}")
            report.check(bool(first(obj, "Resolver Group")), f"    routes to its Resolver Group", first(obj, "Resolver Group"))

    print(f"\n{'All checks passed' if not report.failures else f'{report.failures} check(s) failed'}")
    return 0 if not report.failures else 1


if __name__ == "__main__":
    sys.exit(main())
