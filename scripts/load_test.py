#!/usr/bin/env python3
"""Submit many jobs, wait for them, and print throughput figures. Standard library + Pillow only"""
import argparse
import io
import json
import os
import re
import time
import urllib.request
import uuid
from collections import Counter
from datetime import datetime

from PIL import Image

OPS = [
    ("resize", {"width": 800, "height": 600}),
    ("compress", {"quality": 50}),
    ("convert", {"format": "webp"}),
    ("thumbnail", {"size": 128}),
]


def http(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def make_jpeg(width, height):
    small = Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3))
    img = small.resize((width, height), Image.Resampling.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def encode_multipart(fields, files):
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
    for key, (fname, content, ctype) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                 f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n').encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def queue_length(base):
    try:
        _, body = http("GET", base + "/metrics")
        m = re.search(r"^imgproc_queue_length\s+([0-9.]+)", body.decode(), re.M)
        return int(float(m.group(1))) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--jobs", type=int, default=40)
    ap.add_argument("--width", type=int, default=2000)
    ap.add_argument("--height", type=int, default=1500)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    image = make_jpeg(args.width, args.height)
    print(f"Test image: {args.width}x{args.height}, {len(image) // 1024} KB; submitting {args.jobs} jobs")

    t0 = time.time()
    ids = []
    for i in range(args.jobs):
        op, params = OPS[i % len(OPS)]
        body, ctype = encode_multipart(
            {"operation": op, "params": json.dumps(params)},
            {"file": (f"test_{i}.jpg", image, "image/jpeg")},
        )
        _, resp = http("POST", base + "/jobs", body, {"Content-Type": ctype})
        ids.append(json.loads(resp)["job_id"])
    print(f"All jobs submitted in {time.time() - t0:.1f}s")

    pending, results = set(ids), {}
    while pending and time.time() - t0 < args.timeout:
        for jid in list(pending):
            _, resp = http("GET", f"{base}/jobs/{jid}")
            job = json.loads(resp)
            if job["status"] in ("completed", "failed"):
                results[jid] = job
                pending.discard(jid)
        print(f"\r  done {len(results)}/{len(ids)}   queue length: {queue_length(base)}      ", end="", flush=True)
        time.sleep(1)
    total = time.time() - t0
    print()

    completed = [j for j in results.values() if j["status"] == "completed"]
    failed = [j for j in results.values() if j["status"] == "failed"]
    latencies = [
        (datetime.fromisoformat(j["finished_at"]) - datetime.fromisoformat(j["created_at"])).total_seconds()
        for j in completed
    ]
    print("\n=== RESULTS ===")
    print(f"Total wall time : {total:.1f}s")
    print(f"Completed       : {len(completed)}   Failed: {len(failed)}   Unfinished: {len(pending)}")
    print(f"Throughput      : {len(completed) / total * 60:.1f} jobs/min")
    if latencies:
        print(f"Latency (submit->done): avg {sum(latencies) / len(latencies):.1f}s, max {max(latencies):.1f}s")
    print("Jobs per worker :")
    for worker, n in Counter(j["worker"] for j in completed).most_common():
        print(f"   {worker}: {n}")
    retried = [j for j in completed if j["attempts"] > 1]
    print(f"Jobs that needed more than one attempt: {len(retried)}")


if __name__ == "__main__":
    main()