-- Records are keyed by the immutable Technology One (SM) source ID. `updated_at` is the
-- source system's timestamp; `synced_at` is when the EDW last changed the row.

CREATE TABLE IF NOT EXISTS students (
    student_id  TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    status      TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staff (
    staff_id    TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS courses (
    course_id   TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    title       TEXT NOT NULL,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
