"""Deterministic synthetic seed data. Every name and email is obviously fake:
first names are NATO phonetic words, surnames are placeholder words, and all
emails use the reserved `.example` TLD."""

from datetime import datetime, timezone

from .common import log, pool

SEED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)

FIRST_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
    "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "Xray",
]
LAST_NAMES = [
    "Sample", "Placeholder", "Mockley", "Testwell", "Fakeson", "Dummyfield",
    "Synthetica", "Exampleton", "Stubbs", "Fixture", "Lorem", "Ipsum",
]
DEPARTMENTS = ["Mathematics", "Computer Science", "History", "Biology", "Business"]
STUDENT_STATUSES = ["active"] * 5 + ["inactive", "graduated", "suspended"]
STAFF_STATUSES = ["active"] * 4 + ["on_leave", "inactive"]

COURSES = [
    ("MATH101", "Introduction to Synthetic Calculus", "Mathematics"),
    ("MATH201", "Linear Algebra for Mock Data", "Mathematics"),
    ("CS101", "Programming Fundamentals (Sample)", "Computer Science"),
    ("CS205", "Data Structures Placeholder", "Computer Science"),
    ("CS310", "Integration Patterns Demo", "Computer Science"),
    ("HIST110", "History of Fictional Institutions", "History"),
    ("HIST220", "Example Civilisations", "History"),
    ("BIO101", "Biology of Test Organisms", "Biology"),
    ("BIO230", "Mock Genetics", "Biology"),
    ("BUS100", "Business Basics (Synthetic)", "Business"),
    ("BUS250", "Placeholder Accounting", "Business"),
    ("BUS300", "Strategy for Sample Companies", "Business"),
]

STUDENT_COUNT = 24
STAFF_COUNT = 12


def students():
    for i in range(STUDENT_COUNT):
        n = i + 1
        yield (
            f"STU-{n:04d}",
            FIRST_NAMES[i % len(FIRST_NAMES)],
            LAST_NAMES[(i * 5) % len(LAST_NAMES)],
            f"student{n:04d}@sis.example",
            STUDENT_STATUSES[i % len(STUDENT_STATUSES)],
            SEED_TIMESTAMP,
        )


def staff():
    for i in range(STAFF_COUNT):
        n = i + 1
        yield (
            f"STF-{n:04d}",
            FIRST_NAMES[(i * 7 + 3) % len(FIRST_NAMES)],
            LAST_NAMES[(i * 3 + 1) % len(LAST_NAMES)],
            f"staff{n:04d}@sis.example",
            DEPARTMENTS[i % len(DEPARTMENTS)],
            STAFF_STATUSES[i % len(STAFF_STATUSES)],
            SEED_TIMESTAMP,
        )


def courses():
    for i, (code, title, department) in enumerate(COURSES):
        yield (f"CRS-{i + 1:04d}", code, title, department, "active", SEED_TIMESTAMP)


SEEDS = {
    "students": ("INSERT INTO students (student_id, first_name, last_name, email, status, updated_at)"
                 " VALUES (%s, %s, %s, %s, %s, %s)", students),
    "staff": ("INSERT INTO staff (staff_id, first_name, last_name, email, department, status, updated_at)"
              " VALUES (%s, %s, %s, %s, %s, %s, %s)", staff),
    "courses": ("INSERT INTO courses (course_id, code, title, department, status, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)", courses),
}


def seed_if_empty() -> None:
    with pool.connection() as conn:
        for table, (statement, rows) in SEEDS.items():
            if conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]:
                continue
            with conn.cursor() as cur:
                cur.executemany(statement, list(rows()))
            log.info("seeded table", extra={"fields": {"table": table}})
