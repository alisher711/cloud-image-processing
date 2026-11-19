"""PostgreSQL access: job records and their status."""
import logging
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                UUID PRIMARY KEY,
    original_filename TEXT NOT NULL,
    operation         TEXT NOT NULL,
    parameters        JSONB NOT NULL DEFAULT '{}'::jsonb,
    status            TEXT NOT NULL DEFAULT 'queued',
    input_path        TEXT NOT NULL,
    result_path       TEXT,
    error             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    worker            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
"""


def connect():
    return psycopg.connect(
        **config.db_kwargs(), autocommit=True, row_factory=dict_row, connect_timeout=5
    )


def wait_for_db(timeout=120):
    deadline = time.time() + timeout
    while True:
        try:
            with connect() as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            if time.time() > deadline:
                raise
            log.warning("Database not ready (%s); retrying", exc)
            time.sleep(2)


def init_schema():
    # Advisory lock: several replicas may start at the same time.
    with connect() as conn:
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            conn.execute(SCHEMA)
        finally:
            conn.execute("SELECT pg_advisory_unlock(727001)")


def ping():
    with connect() as conn:
        conn.execute("SELECT 1")


def insert_job(job_id, filename, operation, parameters, input_path):
    with connect() as conn:
        conn.execute(
            """INSERT INTO jobs (id, original_filename, operation, parameters, input_path)
               VALUES (%s::uuid, %s, %s, %s, %s)""",
            (job_id, filename, operation, Jsonb(parameters), input_path),
        )


def get_job(job_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE id = %s::uuid", (job_id,)
        ).fetchone()


def list_jobs(limit=20):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()


def count_by_status():
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, count(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def claim_job(job_id, worker):
    """Mark a job as processing. Returns the row, or None if the job is
    unknown or already finished (e.g. a duplicate/redelivered message).
    A job already in 'processing' CAN be claimed again: that is the retry
    path after a worker died without acknowledging the message."""
    with connect() as conn:
        return conn.execute(
            """UPDATE jobs
               SET status = 'processing', attempts = attempts + 1,
                   worker = %s, started_at = now(), error = NULL
               WHERE id = %s::uuid AND status IN ('queued', 'processing')
               RETURNING *""",
            (worker, job_id),
        ).fetchone()


def complete_job(job_id, result_path):
    # Safe to repeat: the same values are written each time.
    with connect() as conn:
        conn.execute(
            """UPDATE jobs SET status = 'completed', result_path = %s,
                   error = NULL, finished_at = now()
               WHERE id = %s::uuid""",
            (result_path, job_id),
        )


def fail_job(job_id, error):
    with connect() as conn:
        conn.execute(
            """UPDATE jobs SET status = 'failed', error = %s, finished_at = now()
               WHERE id = %s::uuid AND status <> 'completed'""",
            (error[:500], job_id),
        )