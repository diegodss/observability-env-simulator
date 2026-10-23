"""Mock EDW, the enterprise data warehouse (target): receives replicated
students, staff and courses through idempotent bulk-upsert endpoints."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from psycopg import sql
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .common import (
    APP_NAME, ApiError, apply_migrations, database_ready, install_error_handlers,
    install_http_metrics, log, open_database, pool, refresh_state_metrics, require_token,
)

MAX_BULK_RECORDS = 5000

Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class Student(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: Id
    first_name: Name
    last_name: Name
    email: Email
    status: Short
    updated_at: AwareDatetime


class Staff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staff_id: Id
    first_name: Name
    last_name: Name
    email: Email
    department: Text
    status: Short
    updated_at: AwareDatetime


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: Id
    code: Short
    title: Text
    department: Text
    status: Short
    updated_at: AwareDatetime


def bulk_model(record_model: type[BaseModel], id_field: str) -> type[BaseModel]:
    class BulkRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        records: list[record_model] = Field(max_length=MAX_BULK_RECORDS)

        @model_validator(mode="after")
        def unique_ids(self):
            ids = [getattr(r, id_field) for r in self.records]
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"duplicate {id_field} values in payload: {duplicates}")
            return self

    BulkRequest.__name__ = f"{record_model.__name__}BulkRequest"
    return BulkRequest


RESOURCES = {
    "students": ("student_id", Student),
    "staff": ("staff_id", Staff),
    "courses": ("course_id", Course),
}

UPSERTED = Counter(
    "edw_bulk_upsert_records_total",
    "Records processed by bulk upsert endpoints",
    ["app", "collection", "result"],
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    open_database()
    apply_migrations()
    log.info("edw started")
    yield
    pool.close()


app = FastAPI(title="Mock EDW", version="1.0.0", lifespan=lifespan)
install_error_handlers(app)
install_http_metrics(app)


@app.get("/health/live")
def live():
    return {"status": "ok", "app": APP_NAME}


@app.get("/health/ready")
def ready():
    if database_ready():
        return {"status": "ready", "app": APP_NAME}
    return JSONResponse(status_code=503, content={"status": "not_ready", "app": APP_NAME, "reason": "database_unavailable"})


@app.get("/metrics")
def metrics():
    refresh_state_metrics(list(RESOURCES))
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def register(collection: str, id_column: str, record_model: type[BaseModel]) -> None:
    fields = list(record_model.model_fields)
    data_fields = [f for f in fields if f != id_column]
    columns = [*fields, "synced_at"]
    request_model = bulk_model(record_model, id_column)

    select = sql.SQL("SELECT {cols} FROM {table}").format(
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)), table=sql.Identifier(collection)
    )
    # Unchanged rows are skipped by the WHERE clause, so RETURNING tells us
    # whether each record was inserted, updated, or left as is.
    upsert = sql.SQL(
        "INSERT INTO {table} AS t ({fields}) VALUES ({values}) "
        "ON CONFLICT ({id}) DO UPDATE SET {assignments}, synced_at = now() "
        "WHERE ({current}) IS DISTINCT FROM ({incoming}) "
        "RETURNING (xmax = 0) AS inserted"
    ).format(
        table=sql.Identifier(collection),
        fields=sql.SQL(", ").join(map(sql.Identifier, fields)),
        values=sql.SQL(", ").join(sql.Placeholder() * len(fields)),
        id=sql.Identifier(id_column),
        assignments=sql.SQL(", ").join(sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(f)) for f in data_fields),
        current=sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(f)) for f in data_fields),
        incoming=sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(f)) for f in data_fields),
    )

    def list_records():
        with pool.connection() as conn:
            items = conn.execute(select + sql.SQL(" ORDER BY {id}").format(id=sql.Identifier(id_column))).fetchall()
        return {"items": items, "count": len(items)}

    def get_record(record_id: str):
        with pool.connection() as conn:
            row = conn.execute(
                select + sql.SQL(" WHERE {id} = %s").format(id=sql.Identifier(id_column)), (record_id,)
            ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", f"{collection} record '{record_id}' not found")
        return row

    def bulk_upsert(payload: request_model, _: None = Depends(require_token)):
        result = {"inserted": 0, "updated": 0, "unchanged": 0}
        with pool.connection() as conn, conn.transaction():
            for record in payload.records:
                values = record.model_dump()
                row = conn.execute(upsert, [values[f] for f in fields]).fetchone()
                if row is None:
                    result["unchanged"] += 1
                elif row["inserted"]:
                    result["inserted"] += 1
                else:
                    result["updated"] += 1
        for outcome, count in result.items():
            UPSERTED.labels(APP_NAME, collection, outcome).inc(count)
        log.info("bulk upsert", extra={"fields": {"collection": collection, "received": len(payload.records), **result}})
        return {"collection": collection, "received": len(payload.records), **result}

    app.add_api_route(f"/api/{collection}", list_records, methods=["GET"], tags=[collection])
    app.add_api_route(f"/api/{collection}/bulk", bulk_upsert, methods=["PUT"], tags=[collection])
    app.add_api_route(f"/api/{collection}/{{record_id}}", get_record, methods=["GET"], tags=[collection])


for _collection, (_id, _model) in RESOURCES.items():
    register(_collection, _id, _model)
