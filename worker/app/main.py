"""Queue worker: takes a job from RabbitMQ, processes the image, records the result"""
import json
import logging
import os
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika
from PIL import UnidentifiedImageError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from common import config, db, messaging, processing, storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

WORKER_NAME = os.environ.get("HOSTNAME") or socket.gethostname()  # = Pod name on Kubernetes
HEALTH_MAX_AGE = 120  # seconds without a heartbeat before /healthz reports failure

JOBS = Counter("worker_jobs", "Jobs handled by this worker", ["outcome"])
DURATION = Histogram(
    "worker_job_duration_seconds", "Time to process one job",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
BUSY = Gauge("worker_busy", "1 while the worker is processing a job")
for _outcome in ("completed", "failed", "skipped", "retry"):
    JOBS.labels(_outcome)

STOP = threading.Event()
state = {"last_beat": time.time()}

# Errors that retrying cannot fix: the job is marked failed immediately.
PERMANENT_ERRORS = (ValueError, FileNotFoundError, UnidentifiedImageError)


def beat():
    state["last_beat"] = time.time()


def process_job(job_id):
    """Process one job. Returns 'completed', 'failed' or 'skipped'.
    Raises on transient errors so the caller can requeue the message."""
    job = db.claim_job(job_id, WORKER_NAME)
    if job is None:
        log.info("Job %s unknown or already finished; skipping duplicate", job_id)
        JOBS.labels("skipped").inc()
        return "skipped"
    if job["attempts"] > config.max_attempts():
        db.fail_job(job_id, "Gave up after too many attempts")
        JOBS.labels("failed").inc()
        return "failed"

    log.info("Job %s: %s (attempt %s)", job_id, job["operation"], job["attempts"])
    start = time.perf_counter()
    BUSY.set(1)
    try:
        delay = config.processing_delay()
        if delay > 0:
            time.sleep(delay)  # simulates heavier processing for demos
        src = storage.abs_path(job["input_path"])
        final = processing.process_image(
            src, storage.result_base(job_id), job["operation"], job["parameters"]
        )
        db.complete_job(job_id, storage.rel_path(final))
    except PERMANENT_ERRORS as exc:
        log.warning("Job %s failed permanently: %s", job_id, exc)
        db.fail_job(job_id, str(exc))
        JOBS.labels("failed").inc()
        return "failed"
    except Exception:
        if job["attempts"] >= config.max_attempts():
            log.exception("Job %s failed after %s attempts", job_id, job["attempts"])
            db.fail_job(job_id, "Gave up after repeated errors")
            JOBS.labels("failed").inc()
            return "failed"
        raise
    finally:
        BUSY.set(0)

    DURATION.observe(time.perf_counter() - start)
    JOBS.labels("completed").inc()
    log.info("Job %s completed", job_id)
    return "completed"


def consume_forever():
    conn = messaging.connect()
    ch = conn.channel()
    messaging.declare(ch)
    ch.basic_qos(prefetch_count=1)  # one unacknowledged job at a time per worker
    log.info("Worker %s waiting for jobs", WORKER_NAME)
    try:
        for method, _props, body in ch.consume(config.QUEUE_NAME, inactivity_timeout=1):
            beat()
            if STOP.is_set():
                break
            if method is None:
                continue
            try:
                job_id = json.loads(body)["job_id"]
            except Exception:
                log.error("Discarding malformed message: %r", body)
                ch.basic_ack(method.delivery_tag)
                continue
            try:
                process_job(job_id)
            except Exception:
                log.exception("Transient error on job %s; requeueing", job_id)
                JOBS.labels("retry").inc()
                time.sleep(2)
                ch.basic_nack(method.delivery_tag, requeue=True)
                continue
            # Acknowledge ONLY after the result is stored. If this worker dies before
            # this line, RabbitMQ redelivers the message to another worker.
            ch.basic_ack(method.delivery_tag)
    finally:
        try:
            ch.cancel()
            conn.close()
        except Exception:
            pass


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body, ctype, code = generate_latest(), CONTENT_TYPE_LATEST, 200
        elif self.path.startswith("/healthz"):
            fresh = time.time() - state["last_beat"] < HEALTH_MAX_AGE
            body, ctype, code = (b"ok" if fresh else b"stale"), "text/plain", (200 if fresh else 500)
        else:
            body, ctype, code = b"not found", "text/plain", 404
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    server = ThreadingHTTPServer(("0.0.0.0", 8001), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    db.wait_for_db()
    db.init_schema()
    while not STOP.is_set():
        beat()
        try:
            consume_forever()
        except (pika.exceptions.AMQPError, OSError) as exc:
            log.warning("RabbitMQ connection problem (%s); reconnecting in 3s", exc)
            time.sleep(3)
    log.info("Worker stopped cleanly")


if __name__ == "__main__":
    main()