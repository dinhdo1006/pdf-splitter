"""
HTTP API cho đội web/backend gọi worker (không gọi CLI Python trực tiếp).

    python api.py
    POST /api/jobs            1 PDF (tên có khoảng trắng vẫn được)
    POST /api/jobs/batch      folder nhiều PDF
    GET  /api/jobs/{job_id}
    GET  /health
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
from minio_store import MinioSettings, MinioStore, sanitize_job_id

_running: set[str] = set()


def _store() -> MinioStore:
    store = MinioStore(MinioSettings.from_env())
    store.ensure_bucket()
    return store


class StartJobBody(BaseModel):
    job_id: Optional[str] = Field(
        None,
        description="Tên file không đuôi .pdf — cho phép khoảng trắng",
    )
    object_key: Optional[str] = Field(
        None,
        description="Key MinIO đầy đủ, vd. inbox/Ho so Nguyen Van A.pdf",
    )
    pages: Optional[int] = Field(None, ge=1)
    dpi: int = Field(150, ge=72, le=400)
    gpu: bool = False
    cpu: bool = False
    debug: bool = False


class BatchJobBody(BaseModel):
    prefix: str = Field(
        "",
        description="Folder trong inbox, vd. dot_2026 hoặc để trống = cả inbox/",
    )
    pages: Optional[int] = Field(None, ge=1)
    dpi: int = Field(150, ge=72, le=400)
    gpu: bool = False
    cpu: bool = False
    debug: bool = False


app = FastAPI(title="HSDV PDF Splitter", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _spawn_trigger(job_hint: str, body: StartJobBody) -> None:
    log_file = config.PROJECT_ROOT / "logs" / "trigger.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    f_log = open(log_file, "a", encoding="utf-8")

    argv = [
        sys.executable,
        str(config.PROJECT_ROOT / "minio_trigger.py"),
        job_hint,
        "--dpi",
        str(body.dpi),
    ]
    if body.pages is not None:
        argv.extend(["--pages", str(body.pages)])
    if body.gpu or (not body.cpu and os.getenv("WORKER_GPU", "1") == "1"):
        argv.append("--gpu")
    if body.cpu:
        argv.append("--cpu")
    if body.debug:
        argv.append("--debug")

    subprocess.Popen(
        argv,
        cwd=str(config.PROJECT_ROOT),
        stdout=f_log,
        stderr=f_log,
        start_new_session=True,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/jobs", status_code=202)
def start_job(body: StartJobBody) -> dict:
    """1 PDF = 1 hồ sơ. Tên file có khoảng trắng vẫn chạy."""
    if body.gpu and body.cpu:
        raise HTTPException(400, "Không dùng gpu và cpu cùng lúc")
    hint = (body.object_key or body.job_id or "").strip()
    if not hint:
        raise HTTPException(400, "Cần job_id hoặc object_key")

    store = _store()
    try:
        job_id, inbox_key = store.resolve_inbox_pdf(hint)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    if job_id in _running:
        raise HTTPException(409, f"Job {job_id} đang chạy")

    _running.add(job_id)
    try:
        _spawn_trigger(inbox_key, body)
    except Exception:
        _running.discard(job_id)
        raise

    return {
        "job_id": job_id,
        "status": "accepted",
        "poll_url": f"/api/jobs/{job_id}",
        "inbox_key": inbox_key,
        "output_prefix": store.output_prefix_for_job(job_id) + "/",
        "note": "job_id đã bỏ khoảng trắng; poll bằng job_id này",
    }


@app.post("/api/jobs/batch", status_code=202)
def start_batch(body: BatchJobBody) -> dict:
    """Folder nhiều PDF: mỗi file = 1 job, chạy tuần tự."""
    if body.gpu and body.cpu:
        raise HTTPException(400, "Không dùng gpu và cpu cùng lúc")

    store = _store()
    keys = store.list_inbox_pdfs(body.prefix or "")
    if not keys:
        raise HTTPException(
            404,
            f"Không có PDF trong inbox/{body.prefix or ''}",
        )

    argv = [
        sys.executable,
        str(config.PROJECT_ROOT / "minio_run.py"),
        "--poll",
        "--dpi",
        str(body.dpi),
        "--no-preprocess",
    ]
    if body.prefix:
        argv.extend(["--inbox-prefix", body.prefix])
    if body.pages is not None:
        argv.extend(["--pages", str(body.pages)])
    if body.gpu:
        argv.append("--gpu")
    if body.cpu:
        argv.append("--cpu")
    if body.debug:
        argv.append("--debug")

    subprocess.Popen(
        argv,
        cwd=str(config.PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    jobs = []
    for key in keys:
        jid = store.derive_job_id(key)
        jobs.append(
            {
                "job_id": jid,
                "inbox_key": key,
                "poll_url": f"/api/jobs/{jid}",
            }
        )
    return {
        "status": "accepted",
        "count": len(jobs),
        "prefix": body.prefix or "inbox/",
        "jobs": jobs,
    }


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """
    Stream tiến trình realtime theo chuẩn Server-Sent Events (SSE).
    Frontend kết nối:
        const es = new EventSource('http://10.10.4.21:8090/api/jobs/hoso_001/stream');
        es.onmessage = (e) => {
            const data = JSON.parse(e.data);
            console.log(data.progress.percent + "%", data.progress.stage);
        };
    """
    import asyncio
    from fastapi.responses import StreamingResponse

    clean_id = sanitize_job_id(unquote(job_id))
    if not clean_id:
        raise HTTPException(400, "job_id trống")

    store = _store()

    async def event_generator():
        last_json = ""
        while True:
            payload = store.read_status(clean_id) or {
                "job_id": clean_id,
                "status": "queued_or_starting",
            }
            work_dir = store.job_work_dir(clean_id)
            progress_file = work_dir / "output" / "progress.json"
            local_progress = None
            if progress_file.is_file():
                try:
                    local_progress = json.loads(progress_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            if local_progress:
                payload["progress"] = local_progress
                payload["percent"] = local_progress.get("percent", 0.0)
                payload["current_page"] = local_progress.get("current_page", 0)
                payload["total_pages"] = local_progress.get("total_pages", 0)
                payload["stage"] = local_progress.get("stage", "Đang xử lý")
                payload["eta_seconds"] = local_progress.get("eta_seconds", 0.0)
                payload["elapsed_seconds"] = local_progress.get("elapsed_seconds", 0.0)
                payload["docs_found"] = local_progress.get("docs_found", 0)
            elif "progress" not in payload or not payload.get("progress"):
                st = str(payload.get("status") or "")
                if st == "completed":
                    payload["progress"] = {
                        "status": "completed",
                        "percent": 100.0,
                        "stage": "Hoàn thành",
                    }
                    payload["percent"] = 100.0
                    payload["stage"] = "Hoàn thành"
                elif st == "failed":
                    payload["progress"] = {
                        "status": "failed",
                        "percent": 0.0,
                        "stage": "Thất bại: " + str(payload.get("error") or ""),
                    }
                    payload["percent"] = 0.0
                    payload["stage"] = "Thất bại"
                else:
                    payload["progress"] = {
                        "status": st or "running",
                        "percent": 0.0,
                        "stage": "Đang khởi tạo",
                    }
                    payload["percent"] = 0.0
                    payload["stage"] = "Đang khởi tạo"

            current_json = json.dumps(payload, ensure_ascii=False)
            if current_json != last_json:
                last_json = current_json
                yield f"data: {current_json}\n\n"

            st = str(payload.get("status") or "")
            if st in {"completed", "failed"}:
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}/progress")
def get_job_progress(job_id: str) -> dict:
    """Endpoint chuyên biệt lấy thông tin % tiến độ, số trang và thời gian còn lại."""
    return get_job(job_id)


@app.get("/api/jobs/{job_id:path}")
def get_job(job_id: str) -> dict:
    job_id = sanitize_job_id(unquote(job_id))
    if not job_id:
        raise HTTPException(400, "job_id trống")
    store = _store()
    payload = store.read_status(job_id)

    # Đọc progress.json từ work_dir nếu đang chạy để có data realtime tức thì
    work_dir = store.job_work_dir(job_id)
    progress_file = work_dir / "output" / "progress.json"
    local_progress = None
    if progress_file.is_file():
        try:
            local_progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if payload is None:
        try:
            jid, inbox_key = store.resolve_inbox_pdf(job_id)
            cur_prog = local_progress or {
                "status": "queued_or_starting",
                "percent": 0.0,
                "stage": "Đang chờ worker tiếp nhận",
                "current_page": 0,
                "total_pages": 0,
            }
            return {
                "job_id": jid,
                "status": "queued_or_starting",
                "percent": cur_prog.get("percent", 0.0),
                "current_page": cur_prog.get("current_page", 0),
                "total_pages": cur_prog.get("total_pages", 0),
                "stage": cur_prog.get("stage", "Đang chờ worker tiếp nhận"),
                "eta_seconds": cur_prog.get("eta_seconds", 0.0),
                "elapsed_seconds": cur_prog.get("elapsed_seconds", 0.0),
                "input_key": inbox_key,
                "progress": cur_prog,
                "message": "Đã có PDF inbox, đang chờ xử lý.",
            }
        except FileNotFoundError:
            raise HTTPException(404, f"Không thấy job {job_id}") from None

    if local_progress:
        payload["progress"] = local_progress
        payload["percent"] = local_progress.get("percent", 0.0)
        payload["current_page"] = local_progress.get("current_page", 0)
        payload["total_pages"] = local_progress.get("total_pages", 0)
        payload["stage"] = local_progress.get("stage", "Đang xử lý")
        payload["eta_seconds"] = local_progress.get("eta_seconds", 0.0)
        payload["elapsed_seconds"] = local_progress.get("elapsed_seconds", 0.0)
        payload["docs_found"] = local_progress.get("docs_found", 0)
    elif "progress" not in payload or not payload.get("progress"):
        st = str(payload.get("status") or "")
        if st == "completed":
            payload["progress"] = {
                "status": "completed",
                "percent": 100.0,
                "stage": "Hoàn thành",
            }
            payload["percent"] = 100.0
            payload["stage"] = "Hoàn thành"
        elif st == "failed":
            payload["progress"] = {
                "status": "failed",
                "percent": 0.0,
                "stage": "Thất bại: " + str(payload.get("error") or ""),
            }
            payload["percent"] = 0.0
            payload["stage"] = "Thất bại"
        else:
            payload["progress"] = {
                "status": st or "running",
                "percent": 0.0,
                "stage": "Đang khởi tạo",
            }
            payload["percent"] = 0.0
            payload["stage"] = "Đang khởi tạo"

    st = str(payload.get("status") or "")
    if st in {"completed", "failed"}:
        _running.discard(job_id)
    return payload


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SPLITTER_API_HOST", "0.0.0.0")
    port = int(os.getenv("SPLITTER_API_PORT", "8090"))
    uvicorn.run("api:app", host=host, port=port, reload=False)
