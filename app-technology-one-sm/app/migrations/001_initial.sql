CREATE TABLE IF NOT EXISTS students (
    student_id  TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'graduated', 'suspended')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staff (
    staff_id    TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'on_leave')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS courses (
    course_id   TEXT PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'archived')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
