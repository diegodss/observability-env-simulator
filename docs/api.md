# API contract

Both applications are FastAPI services; interactive OpenAPI docs are at
`http://localhost:8001/docs` (Technology One (SM) mock) and `http://localhost:8002/docs` (EDW mock).
The contract below is what the AppEvents workers depend on. Either mock can be replaced by the real
system, or by an adapter in front of it, that honours its section.

## Conventions

- JSON in and out, UTF-8. Timestamps are ISO 8601 with offset, always UTC (`2026-01-01T00:00:00+00:00`).
- Records are keyed by the **immutable Technology One (SM) source ID** (`student_id`, `staff_id`, `course_id`).
- Write endpoints (Technology One (SM) `PATCH`, EDW bulk `PUT`) require `Authorization: Bearer <API_TOKEN>`
  (default `dev-poc-token`; set `API_TOKEN=` empty to disable). Reads are open.
- Errors always use one envelope:

```json
{"error": {"code": "validation_error", "message": "Request payload failed validation", "details": [ ... ]}}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `validation_error` | PATCH with no fields |
| 401 | `unauthorized` | missing/wrong bearer token on a write endpoint |
| 404 | `not_found` | unknown record or route |
| 405 | `method_not_allowed` | wrong method |
| 409 | `conflict` | Technology One (SM) PATCH would duplicate a unique `email`/`code` |
| 422 | `validation_error` | malformed body, unknown fields, wrong types, bad enum, duplicate IDs in a bulk payload; `details` lists each problem |
| 503 | `database_unavailable` | the app's own database is unreachable |
| 500 | `internal_error` | unexpected error |

## Record shapes

| Collection | Fields |
|---|---|
| students | `student_id`, `first_name`, `last_name`, `email`, `status` (`active` `inactive` `graduated` `suspended`), `updated_at` |
| staff | `staff_id`, `first_name`, `last_name`, `email`, `department`, `status` (`active` `inactive` `on_leave`), `updated_at` |
| courses | `course_id`, `code`, `title`, `department`, `status` (`active` `inactive` `archived`), `updated_at` |

Seed IDs: `STU-0001`…`STU-0024`, `STF-0001`…`STF-0012`, `CRS-0001`…`CRS-0012`. All names are
NATO-alphabet/placeholder words and all emails end in `@technology-one-sm.example` (reserved TLD).

## Source — Technology One (SM) mock (`http://technology-one-sm:8000` in Docker, `http://localhost:8001` on the host)

| Method | Path | Description |
|---|---|---|
| GET | `/api/{collection}` | `{"items": [...], "count": n}` ordered by ID |
| GET | `/api/{collection}/{id}` | one record |
| PATCH | `/api/{collection}/{id}` | partial update of non-ID fields; sets `updated_at = now()`; returns the record |
| GET | `/health/live` | 200 while the process runs |
| GET | `/health/ready` | 200 when the database answers, else 503 |
| GET | `/metrics` | Prometheus text format |

`{collection}` is `students`, `staff` or `courses`. Example:

```bash
curl -X PATCH localhost:8001/api/courses/CRS-0001 \
  -H 'Authorization: Bearer dev-poc-token' -H 'Content-Type: application/json' \
  -d '{"title": "Renamed course"}'
```

## Target — EDW mock (`http://edw:8000` in Docker, `http://localhost:8002` on the host)

| Method | Path | Description |
|---|---|---|
| PUT | `/api/{collection}/bulk` | idempotent bulk upsert (below) |
| GET | `/api/{collection}` | `{"items": [...], "count": n}`; items carry the source fields plus `synced_at` |
| GET | `/api/{collection}/{id}` | one record |
| GET | `/health/live`, `/health/ready`, `/metrics` | as for Technology One (SM) |

### Bulk upsert

Request — every record must be complete (all fields of its shape, nothing extra), at most 5000 per call, no duplicate IDs:

```json
{"records": [{"student_id": "STU-0001", "first_name": "Alpha", "last_name": "Sample",
              "email": "student0001@technology-one-sm.example", "status": "active",
              "updated_at": "2026-01-01T00:00:00+00:00"}]}
```

Response 200:

```json
{"collection": "students", "received": 1, "inserted": 0, "updated": 0, "unchanged": 1}
```

Semantics: insert unknown IDs, overwrite known IDs whose data differs, leave identical rows
untouched (`synced_at` unchanged). The whole batch is one transaction. Records missing from a
payload are **not** deleted. Status values are accepted as free text (≤32 chars) so a richer source
can be plugged in without changing the EDW mock.
