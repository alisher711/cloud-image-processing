"""FastAPI service: accepts uploads, creates jobs, exposes status and results"""
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Gauge, Histogram,
                               generate_latest)

from common import config, db, messaging, processing, storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
QUEUE_LENGTH = Gauge("imgproc_queue_length", "Jobs waiting in the queue")
JOBS_BY_STATUS = Gauge("imgproc_jobs", "Jobs by status", ["status"])


@asynccontextmanager
async def lifespan(app):
    db.wait_for_db()
    db.init_schema()
    yield


app = FastAPI(title="Cloud Image Processing", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    return response


def job_view(row):
    jid = str(row["id"])
    return {
        "job_id": jid,
        "filename": row["original_filename"],
        "operation": row["operation"],
        "parameters": row["parameters"],
        "status": row["status"],
        "attempts": row["attempts"],
        "worker": row["worker"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "result_url": f"/jobs/{jid}/result" if row["status"] == "completed" else None,
    }


def _valid_uuid(value):
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}  # liveness: the process is running


@app.get("/readyz")
def readyz():
    try:  # readiness: dependencies reachable
        db.ping()
        messaging.ping()
    except Exception as exc:
        log.warning("Not ready: %s", exc)
        raise HTTPException(status_code=503, detail="dependency unavailable")
    return {"status": "ready"}


@app.post("/jobs", status_code=202)
def create_job(
    file: UploadFile = File(...),
    operation: str = Form(...),
    params: str = Form("{}"),
):
    limit = config.max_upload_bytes()
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File larger than {limit // (1024 * 1024)} MB")
    try:
        fmt = processing.inspect_upload(data)
        clean = processing.validate_params(operation, json.loads(params))
    except ValueError as exc:  # JSONDecodeError is a ValueError too
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = str(uuid.uuid4())
    filename = os.path.basename(file.filename or "upload")[:200] or "upload"
    input_path = storage.save_upload(job_id, processing.FORMAT_EXT[fmt], data)
    db.insert_job(job_id, filename, operation, clean, input_path)
    try:
        messaging.publish_job(job_id)
    except Exception:
        log.exception("Could not publish job %s", job_id)
        db.fail_job(job_id, "Queue unavailable")
        raise HTTPException(status_code=503, detail="Queue unavailable, try again")
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs")
def list_jobs(limit: int = 20):
    return [job_view(r) for r in db.list_jobs(max(1, min(limit, 100)))]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    row = db.get_job(_valid_uuid(job_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_view(row)


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    row = db.get_job(_valid_uuid(job_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {row['status']}, not completed")
    path = storage.abs_path(row["result_path"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Result file missing")
    ext = path.rsplit(".", 1)[-1]
    stem = os.path.splitext(row["original_filename"])[0] or "image"
    return FileResponse(
        path,
        media_type=processing.EXT_MIME.get(ext, "application/octet-stream"),
        filename=f"{stem}_{row['operation']}.{ext}",
    )


@app.get("/metrics")
def metrics():
    try:
        QUEUE_LENGTH.set(messaging.queue_length())
    except Exception as exc:
        log.warning("queue length unavailable: %s", exc)
    try:
        counts = db.count_by_status()
        for status in ("queued", "processing", "completed", "failed"):
            JOBS_BY_STATUS.labels(status).set(counts.get(status, 0))
    except Exception as exc:
        log.warning("job counts unavailable: %s", exc)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)