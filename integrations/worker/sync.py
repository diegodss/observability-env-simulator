"""Core of one integration run: read one collection from the SIS, push it to
the matching LMS bulk endpoint, and classify any failure by the system at fault."""

import os
import time
from dataclasses import dataclass

import httpx

SOURCE = "sis"
TARGET = "lms"

ID_FIELDS = {"students": "student_id", "staff": "staff_id", "courses": "course_id"}


@dataclass(frozen=True)
class Config:
    integration: str
    sis_base_url: str = "http://sis:8000"
    lms_base_url: str = "http://lms:8000"
    api_token: str = ""
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    max_retries: int = 1
    retry_backoff_seconds: float = 1.0
    metrics_port: int = 9100
    sis_startup_wait_seconds: float = 60.0

    @property
    def service(self) -> str:
        return f"sync-{self.integration}"

    @classmethod
    def from_env(cls) -> "Config":
        integration = os.environ.get("INTEGRATION", "")
        if integration not in ID_FIELDS:
            raise SystemExit(f"INTEGRATION must be one of {sorted(ID_FIELDS)}, got {integration!r}")
        return cls(
            integration=integration,
            sis_base_url=os.getenv("SIS_BASE_URL", cls.sis_base_url).rstrip("/"),
            lms_base_url=os.getenv("LMS_BASE_URL", cls.lms_base_url).rstrip("/"),
            api_token=os.getenv("API_TOKEN", ""),
            interval_seconds=float(os.getenv("SYNC_INTERVAL_SECONDS", "30")),
            timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "5")),
            max_retries=int(os.getenv("HTTP_MAX_RETRIES", "1")),
            retry_backoff_seconds=float(os.getenv("HTTP_RETRY_BACKOFF_SECONDS", "1")),
            metrics_port=int(os.getenv("METRICS_PORT", "9100")),
            sis_startup_wait_seconds=float(os.getenv("SIS_STARTUP_WAIT_SECONDS", "60")),
        )


class SyncError(Exception):
    """A classified run failure. `system` is the system at fault (sis or lms)."""

    def __init__(self, error_type: str, message: str, *, system: str, operation: str,
                 http_status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.system = system
        self.operation = operation
        self.http_status = http_status
        self.retryable = retryable


def _role(system: str) -> str:
    return "target" if system == TARGET else "source"


def _classify(exc: Exception, system: str, operation: str) -> SyncError:
    role = _role(system)
    if isinstance(exc, httpx.TimeoutException):
        return SyncError(f"{role}_timeout", f"{type(exc).__name__}: {exc}", system=system,
                         operation=operation, retryable=True)
    return SyncError(f"{role}_connection_error", f"{type(exc).__name__}: {exc}", system=system,
                     operation=operation, retryable=True)


def _http_error(response: httpx.Response, system: str, operation: str) -> SyncError:
    try:
        error = response.json().get("error", {})
        detail = f"{error.get('code')}: {error.get('message')}"
    except (ValueError, AttributeError):
        detail = response.text[:200]
    error_type = "validation_error" if response.status_code == 422 else f"{_role(system)}_http_error"
    return SyncError(error_type, f"HTTP {response.status_code} from {system}: {detail}", system=system,
                     operation=operation, http_status=response.status_code,
                     retryable=response.status_code >= 500 or response.status_code == 429)


def request_with_retry(client: httpx.Client, config: Config, method: str, url: str, *,
                       system: str, operation: str, on_attempt_failed=None, **kwargs) -> httpx.Response:
    attempts = config.max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code < 400:
                return response
            error = _http_error(response, system, operation)
        except httpx.TransportError as exc:
            error = _classify(exc, system, operation)
        will_retry = error.retryable and attempt < attempts
        if on_attempt_failed:
            on_attempt_failed(error, attempt, will_retry)
        if not will_retry:
            raise error
        time.sleep(config.retry_backoff_seconds * attempt)
    raise AssertionError("unreachable")


def sync_once(client: httpx.Client, config: Config, on_attempt_failed=None) -> dict:
    """Run one sync. Returns the LMS upsert summary; raises SyncError on failure."""
    collection = config.integration
    id_field = ID_FIELDS[collection]

    response = request_with_retry(
        client, config, "GET", f"{config.sis_base_url}/api/{collection}",
        system=SOURCE, operation=f"read {collection} from {SOURCE}", on_attempt_failed=on_attempt_failed,
    )
    try:
        items = response.json()["items"]
        if not isinstance(items, list) or not all(isinstance(i, dict) and i.get(id_field) for i in items):
            raise ValueError(f"every item must be an object with a non-empty {id_field}")
    except (ValueError, KeyError, TypeError) as exc:
        raise SyncError("validation_error", f"Malformed {SOURCE} response: {exc}", system=SOURCE,
                        operation=f"read {collection} from {SOURCE}")

    headers = {"Authorization": f"Bearer {config.api_token}"} if config.api_token else {}
    response = request_with_retry(
        client, config, "PUT", f"{config.lms_base_url}/api/{collection}/bulk",
        system=TARGET, operation=f"upsert {collection} into {TARGET}", on_attempt_failed=on_attempt_failed,
        json={"records": items}, headers=headers,
    )
    summary = response.json()
    summary["records"] = len(items)
    return summary
