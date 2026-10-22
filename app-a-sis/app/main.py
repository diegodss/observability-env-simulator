"""Mock Student Information System (Application A): authoritative source of
synthetic students, staff and courses."""

from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Body, Depends, FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg import sql
from pydantic import BaseModel, ConfigDict, StringConstraints

from .common import (
    APP_NAME, ApiError, apply_migrations, database_ready, install_error_handlers,
    install_http_metrics, log, open_database, pool, refresh_state_metrics, require_token,
)
from .seed import seed_if_empty

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class StudentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: Name | None = None
    last_name: Name | None = None
    email: Email | None = None
    status: Literal["active", "inactive", "graduated", "suspended"] | None = None


class StaffPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: Name | None = None
    last_name: Name | None = None
    email: Email | None = None
    department: Text | None = None
    status: Literal["active", "inactive", "on_leave"] | None = None


class CoursePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Code | None = None
    title: Text | None = None
    department: Text | None = None
    status: Literal["active", "inactive", "archived"] | None = None


RESOURCES = {
    "students": ("student_id", ["student_id", "first_name", "last_name", "email", "status", "updated_at"], StudentPatch),
    "staff": ("staff_id", ["staff_id", "first_name", "last_name", "email", "department", "status", "updated_at"], StaffPatch),
    "courses": ("course_id", ["course_id", "code", "title", "department", "status", "updated_at"], CoursePatch),
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    open_database()
    apply_migrations()
    seed_if_empty()
    log.info("sis started")
    yield
    pool.close()


app = FastAPI(title="Mock SIS", version="1.0.0", lifespan=lifespan)
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


def register(collection: str, id_column: str, columns: list[str], patch_model: type[BaseModel]) -> None:
    select = sql.SQL("SELECT {cols} FROM {table}").format(
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)), table=sql.Identifier(collection)
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

    def patch_record(record_id: str, patch: Annotated[patch_model, Body()], _: None = Depends(require_token)):
        changes = patch.model_dump(exclude_unset=True)
        if not changes:
            raise ApiError(400, "validation_error", "Provide at least one field to update")
        if any(v is None for v in changes.values()):
            raise ApiError(422, "validation_error", "Fields may not be set to null")
        statement = sql.SQL("UPDATE {table} SET {assignments}, updated_at = now() WHERE {id} = %s RETURNING {cols}").format(
            table=sql.Identifier(collection),
            assignments=sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(k)) for k in changes
            ),
            id=sql.Identifier(id_column),
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        try:
            with pool.connection() as conn:
                row = conn.execute(statement, [*changes.values(), record_id]).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "conflict", "Update would violate a uniqueness constraint")
        if row is None:
            raise ApiError(404, "not_found", f"{collection} record '{record_id}' not found")
        log.info("record updated", extra={"fields": {"collection": collection, "record_id": record_id, "fields_changed": sorted(changes)}})
        return row

    app.add_api_route(f"/api/{collection}", list_records, methods=["GET"], tags=[collection])
    app.add_api_route(f"/api/{collection}/{{record_id}}", get_record, methods=["GET"], tags=[collection])
    app.add_api_route(f"/api/{collection}/{{record_id}}", patch_record, methods=["PATCH"], tags=[collection])


for _collection, (_id, _columns, _model) in RESOURCES.items():
    register(_collection, _id, _columns, _model)
