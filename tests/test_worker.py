import io

from PIL import Image

from worker.app import main as wmain


def make_job(job_id, op="resize", params=None, path="uploads/j.png", attempts=1):
    return {"id": job_id, "attempts": attempts, "operation": op,
            "parameters": params or {"width": 10, "height": 5}, "input_path": path}


def write_png(tmp_path):
    (tmp_path / "uploads").mkdir()
    Image.new("RGB", (40, 20), (1, 2, 3)).save(tmp_path / "uploads" / "j.png", format="PNG")


def test_completes_job(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_png(tmp_path)
    done = {}
    monkeypatch.setattr(wmain.db, "claim_job", lambda jid, w: make_job(jid))
    monkeypatch.setattr(wmain.db, "complete_job", lambda jid, rel: done.update(rel=rel))
    assert wmain.process_job("job1") == "completed"
    assert done["rel"] == "results/job1.png"
    assert (tmp_path / "results" / "job1.png").exists()


def test_duplicate_message_is_skipped(monkeypatch):
    monkeypatch.setattr(wmain.db, "claim_job", lambda jid, w: None)  # already completed
    assert wmain.process_job("job1") == "skipped"


def test_corrupt_input_marks_job_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "j.png").write_bytes(b"garbage")
    failed = {}
    monkeypatch.setattr(wmain.db, "claim_job", lambda jid, w: make_job(jid))
    monkeypatch.setattr(wmain.db, "fail_job", lambda jid, err: failed.update(err=err))
    assert wmain.process_job("job1") == "failed"
    assert "err" in failed