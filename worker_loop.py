"""
worker_loop.py — MinIO inbox scanner chạy liên tục.

Cứ mỗi WORKER_POLL_INTERVAL giây (mặc định 30s), quét toàn bộ inbox/
trong MinIO, xử lý các PDF mới, rồi nghỉ và lặp lại.

Chạy bởi Docker service 'worker'.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "30"))  # giây
DPI = int(os.getenv("WORKER_DPI", "150"))
NO_PREPROCESS = os.getenv("WORKER_NO_PREPROCESS", "1") == "1"
USE_GPU = os.getenv("WORKER_GPU", "1") == "1"

PROJECT_ROOT = Path(__file__).parent


def run_poll() -> int:
    argv = [
        sys.executable,
        str(PROJECT_ROOT / "minio_run.py"),
        "--poll",
        "--dpi", str(DPI),
    ]
    if NO_PREPROCESS:
        argv.append("--no-preprocess")
    if USE_GPU:
        argv.append("--gpu")

    logger.info(f"[worker] Quét inbox MinIO... (dpi={DPI}, gpu={USE_GPU})")
    result = subprocess.run(argv, cwd=str(PROJECT_ROOT))
    return result.returncode


def main() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
    )
    logger.add(
        str(PROJECT_ROOT / "logs" / "worker.log"),
        level="DEBUG",
        rotation="50 MB",
        retention="7 days",
        encoding="utf-8",
    )

    logger.info(f"[worker] Khởi động — poll mỗi {POLL_INTERVAL}s")

    while True:
        try:
            rc = run_poll()
            if rc != 0:
                logger.warning(f"[worker] minio_run.py trả về exit code {rc}")
        except Exception as exc:
            logger.exception(f"[worker] Lỗi không mong muốn: {exc}")

        logger.info(f"[worker] Nghỉ {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
