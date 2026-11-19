"""File storage on the shared volume. Paths in the DB are relative to DATA_DIR."""
import os

from . import config


def abs_path(rel):
    base = os.path.realpath(config.data_dir())
    path = os.path.realpath(os.path.join(base, rel))
    if not path.startswith(base + os.sep):
        raise ValueError("Path escapes the data directory")
    return path


def rel_path(absolute):
    return os.path.relpath(absolute, os.path.realpath(config.data_dir()))


def save_upload(job_id, ext, data):
    rel = f"uploads/{job_id}.{ext}"
    path = abs_path(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return rel


def result_base(job_id):
    """Predictable output path (without extension) for a job: needed for idempotency."""
    path = abs_path(f"results/{job_id}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path