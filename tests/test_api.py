import io

from fastapi.testclient import TestClient
from PIL import Image

from api.app import main

client = TestClient(main.app)  # not used as a context manager: no DB needed at startup


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_rejects_non_image():
    r = client.post(
        "/jobs",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"operation": "resize", "params": '{"width": 10, "height": 10}'},
    )
    assert r.status_code == 400


def test_rejects_unknown_operation():
    r = client.post(
        "/jobs",
        files={"file": ("a.png", png_bytes(), "image/png")},
        data={"operation": "blur", "params": "{}"},
    )
    assert r.status_code == 400


def test_accepts_valid_job(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    published = []
    monkeypatch.setattr(main.db, "insert_job", lambda *a, **k: None)
    monkeypatch.setattr(main.messaging, "publish_job", lambda job_id: published.append(job_id))
    r = client.post(
        "/jobs",
        files={"file": ("cat.png", png_bytes(), "image/png")},
        data={"operation": "resize", "params": '{"width": 20, "height": 10}'},
    )
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    assert published == [r.json()["job_id"]]


def test_unknown_job_is_404():
    assert client.get("/jobs/not-a-uuid").status_code == 404