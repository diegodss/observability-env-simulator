"""Infrastructure shared by the Technology One (SM) mock endpoints: config, logging, DB pool,
migrations, error envelope, auth and HTTP/Prometheus instrumentation."""

import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

APP_NAME = os.getenv("APP_NAME", "technology-one-sm")
DATABASE_URL = os.environ["DATABASE_URL"]
API_TOKEN = os.getenv("API_TOKEN", "")
DB_CONNECT_ATTEMPTS = int(os.getenv("DB_CONNECT_ATTEMPTS", "30"))
DB_CONNECT_DELAY_SECONDS = float(os.getenv("DB_CONNECT_DELAY_SECONDS", "2"))
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# --------------------------------------------------------------------------- logging

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": APP_NAME,
            "message": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logging.getLogger(APP_NAME)


log = configure_logging()


# --------------------------------------------------------------------------- database

pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=5,
    open=False,
    kwargs={"row_factory": dict_row, "options": "-c TimeZone=UTC"},
    check=ConnectionPool.check_connection,
)


def open_database() -> None:
    """Open the pool and block until the database answers (bounded retries)."""
    pool.open(wait=False)
    for attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            with pool.connection(timeout=5) as conn:
                conn.execute("SELECT 1")
            log.info("database reachable", extra={"fields": {"attempt": attempt}})
            return
        except (psycopg.OperationalError, PoolTimeout) as exc:
            log.warning(
                "database not reachable yet",
                extra={"fields": {"attempt": attempt, "error_message": str(exc)}},
            )
            time.sleep(DB_CONNECT_DELAY_SECONDS)
    raise RuntimeError(f"database unreachable after {DB_CONNECT_ATTEMPTS} attempts")


def apply_migrations() -> None:
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            log.info("migration applied", extra={"fields": {"migration": path.name}})


def database_ready() -> bool:
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- errors

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status, content=body)


_HTTP_CODES = {
    400: "bad_request", 401: "unauthorized", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return error_response(422, "validation_error", "Request payload failed validation", exc.errors())

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, _HTTP_CODES.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(PoolTimeout)
    async def _db_down(_: Request, exc: Exception):
        log.error("database unavailable", extra={"fields": {"error_message": str(exc)}})
        return error_response(503, "database_unavailable", "The database is not reachable")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return error_response(500, "internal_error", "Unexpected server error")


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Simple shared development token for write endpoints; disabled when API_TOKEN is empty."""
    if not API_TOKEN:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise ApiError(401, "unauthorized", "Missing or invalid bearer token")


# --------------------------------------------------------------------------- metrics

APP_UP = Gauge("app_up", "1 when the application process is running and its database is reachable, else 0", ["app"])
HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests handled", ["app", "method", "route", "status_code"]
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["app", "method", "route"]
)
RECORDS = Gauge("app_records", "Records currently stored, per collection", ["app", "collection"])


def install_http_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def _instrument(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            if path != "/metrics":
                HTTP_REQUESTS.labels(APP_NAME, request.method, path, str(status)).inc()
                HTTP_DURATION.labels(APP_NAME, request.method, path).observe(time.perf_counter() - started)


def refresh_state_metrics(tables: list[str]) -> None:
    """Evaluated at scrape time so /metrics always reflects current readiness."""
    try:
        with pool.connection(timeout=2) as conn:
            for table in tables:
                count = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                RECORDS.labels(APP_NAME, table).set(count)
        APP_UP.labels(APP_NAME).set(1)
    except Exception:
        APP_UP.labels(APP_NAME).set(0)
