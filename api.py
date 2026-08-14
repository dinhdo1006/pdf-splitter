"""
HTTP API cho đội web/backend gọi worker (không gọi CLI Python trực tiếp).

Chạy trên server GPU:
    source .venv/bin/activate
    pip install fastapi uvicorn
    uvicorn api:app --host 0.0.0.0 --port 8090

Web/backend:
    POST /api/jobs          { "job_id": "bdhn_full" }   → 202, chạy nền
    GET  /api/jobs/{job_id}                              → status.json
    GET  /health
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
from minio_store import MinioSettings, MinioStore

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_running: set[str] = set()


def _store() -> MinioStore:
    store = MinioStore(MinioSettings.from_env())
    store.ensure_bucket()
    return store


class StartJobBody(BaseModel):
    job_id: str = Field(..., description="Trùng tên file inbox/{job_id}.pdf")
    pages: Optional[int] = Field(None, ge=1, description="Giới hạn trang (test)")
    dpi: int = Field(150, ge=72, le=400)
    gpu: bool = False
    cpu: bool = False
    debug: bool = False


app = FastAPI(title="HSDV PDF Splitter", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _clean_job_id(raw: str) -> str:
    job_id = (raw or "").strip()
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(
            400,
            "job_id không hợp lệ (chỉ chữ, số, . _ - ; không dùng {job_id} mẫu)",
        )
    return job_id


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/jobs", status_code=202)
def start_job(body: StartJobBody) -> dict:
    """
    Kích hoạt bóc tách. PDF phải đã có trên MinIO: inbox/{job_id}.pdf
    OCR chạy nền — request trả ngay, poll GET /api/jobs/{job_id}
    """
    job_id = _clean_job_id(body.job_id)
    if body.gpu and body.cpu:
        raise HTTPException(400, "Không dùng gpu và cpu cùng lúc")

    store = _store()
    inbox_key = store.inbox_key_for_job(job_id)
    if not store.object_exists(inbox_key):
        raise HTTPException(
            404,
            f"Chưa có file MinIO: s3://{store.settings.bucket}/{inbox_key}",
        )

    if job_id in _running:
        raise HTTPException(409, f"Job {job_id} đang chạy")

    argv = [
        sys.executable,
        str(config.PROJECT_ROOT / "minio_trigger.py"),
        job_id,
        "--dpi",
        str(body.dpi),
    ]
    if body.pages is not None:
        argv.extend(["--pages", str(body.pages)])
    if body.gpu:
        argv.append("--gpu")
    if body.cpu:
        argv.append("--cpu")
    if body.debug:
        argv.append("--debug")

    _running.add(job_id)
    try:
        subprocess.Popen(
            argv,
            cwd=str(config.PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        _running.discard(job_id)
        raise

    return {
        "job_id": job_id,
        "status": "accepted",
        "poll_url": f"/api/jobs/{job_id}",
        "inbox_key": inbox_key,
        "output_prefix": store.output_prefix_for_job(job_id) + "/",
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job_id = _clean_job_id(job_id)
    store = _store()
    payload = store.read_status(job_id)
    if payload is None:
        inbox_key = store.inbox_key_for_job(job_id)
        if store.object_exists(inbox_key):
            return {
                "job_id": job_id,
                "status": "queued_or_starting",
                "input_key": inbox_key,
                "message": "Đã có PDF inbox, chưa có status.json — job mới nhận hoặc chưa start.",
            }
        raise HTTPException(404, f"Không thấy job {job_id}")
    st = str(payload.get("status") or "")
    if st in {"completed", "failed"}:
        _running.discard(job_id)
    return payload


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SPLITTER_API_HOST", "0.0.0.0")
    port = int(os.getenv("SPLITTER_API_PORT", "8090"))
    uvicorn.run("api:app", host=host, port=port, reload=False)
