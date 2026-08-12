"""
Điểm vào Cách A — backend dev gọi sau khi upload inbox/{job_id}.pdf

Usage:
    python minio_trigger.py bdhn_001
    python minio_trigger.py bdhn_001 --pages 10
    python minio_trigger.py bdhn_001 --gpu
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trigger PDF split job (Cách A) — gọi sau upload inbox/{job_id}.pdf",
    )
    parser.add_argument("job_id", help="Job ID (trùng tên file inbox/{job_id}.pdf)")
    parser.add_argument("--pages", type=int, default=None, help="Giới hạn trang (test)")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--gpu", action="store_true", help="Ép GPU")
    parser.add_argument("--cpu", action="store_true", help="Ép CPU")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job_id = args.job_id.strip()
    if not job_id:
        print("job_id không được rỗng", file=sys.stderr)
        return 1

    argv = [
        sys.executable,
        str(config.PROJECT_ROOT / "minio_run.py"),
        "--job-id",
        job_id,
        "--dpi",
        str(args.dpi),
        "--no-preprocess",
    ]
    if args.pages is not None:
        argv.extend(["--pages", str(args.pages)])
    if args.gpu:
        argv.append("--gpu")
    if args.cpu:
        argv.append("--cpu")
    if args.debug:
        argv.append("--debug")

    proc = subprocess.run(argv, cwd=str(config.PROJECT_ROOT))
    return int(proc.returncode or 0)


if __name__ == "__main__":
    sys.exit(main())
