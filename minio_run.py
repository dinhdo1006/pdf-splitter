"""
minio_run.py — Luồng MinIO: inbox PDF → main.py → upload output + status.json

Usage:
    # Tạo bucket (lần đầu)
    python minio_run.py --setup

    # Xử lý 1 file trong inbox (key đầy đủ hoặc chỉ job_id)
    python minio_run.py --key inbox/hoso.pdf
    python minio_run.py --job-id hoso_001

    # Quét toàn bộ inbox/*.pdf
    python minio_run.py --poll

    # Tuỳ chọn pipeline (truyền xuống main.py)
    python minio_run.py --job-id demo --dpi 150 --no-preprocess --cpu
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import config
from minio_store import MinioSettings, MinioStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MinIO wrapper: inbox → PDF splitter CLI → output bucket",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Tạo bucket nếu chưa có (không chạy pipeline)",
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Xử lý tất cả PDF trong prefix inbox/",
    )
    parser.add_argument(
        "--inbox-prefix",
        default="",
        help="Khi --poll: chỉ PDF trong inbox/<prefix>/ (folder nhiều hồ sơ)",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Object key MinIO (vd. inbox/hoso.pdf)",
    )
    parser.add_argument(
        "--job-id",
        default=None,
        help="Job ID — tìm inbox/{job_id}.pdf hoặc dùng làm output prefix",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        default=False,
        help="Chuyển PDF inbox sang archive/ sau khi xử lý (mặc định: False - giữ nguyên file trong inbox/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Ép bóc tách lại kể cả khi job đã có status completed",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Giữ thư mục work_minio/{job_id} sau khi upload",
    )
    parser.add_argument("--dpi", type=int, default=None)
    parser.add_argument("--no-preprocess", action="store_true")
    parser.add_argument("--adaptive-dpi", action="store_true")
    parser.add_argument("--pages", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--m1", default=None)
    parser.add_argument("--m2", default=None)
    parser.add_argument("--m3", default=None)
    parser.add_argument("--m4", default=None)
    parser.add_argument("--m5", default=None)
    parser.add_argument("--cccd", default=None)
    parser.add_argument("--ho-ten", default=None, dest="ho_ten")
    return parser.parse_args()


def pipeline_flags_from_args(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    if args.dpi is not None:
        flags.extend(["--dpi", str(args.dpi)])
    if args.no_preprocess:
        flags.append("--no-preprocess")
    if args.adaptive_dpi:
        flags.append("--adaptive-dpi")
    if args.pages is not None:
        flags.extend(["--pages", str(args.pages)])
    if args.debug:
        flags.append("--debug")
    if args.cpu:
        flags.append("--cpu")
    if args.gpu:
        flags.append("--gpu")
    if args.m1:
        flags.extend(["--m1", args.m1])
    if args.m2:
        flags.extend(["--m2", args.m2])
    if args.m3:
        flags.extend(["--m3", args.m3])
    if args.m4:
        flags.extend(["--m4", args.m4])
    if args.m5:
        flags.extend(["--m5", args.m5])
    if args.cccd:
        flags.extend(["--cccd", args.cccd])
    if args.ho_ten:
        flags.extend(["--ho-ten", args.ho_ten])
    return flags


def build_main_argv(
    args: argparse.Namespace,
    input_pdf: Path,
    output_dir: Path,
) -> list[str]:
    argv = [
        sys.executable,
        str(config.PROJECT_ROOT / "main.py"),
        "-i",
        str(input_pdf),
        "-o",
        str(output_dir),
    ]
    argv.extend(pipeline_flags_from_args(args))
    return argv


def resolve_input_key(store: MinioStore, args: argparse.Namespace) -> tuple[str, str]:
    """Returns (job_id, inbox_object_key)."""
    if args.key:
        key = args.key.strip().replace("\\", "/")
        if not key.lower().endswith(".pdf"):
            raise ValueError("--key phải trỏ tới file .pdf")
        job_id = args.job_id or store.derive_job_id(key)
        return job_id, key

    if args.job_id:
        try:
            job_id, key = store.resolve_inbox_pdf(args.job_id)
        except FileNotFoundError as exc:
            raise FileNotFoundError(str(exc)) from exc
        return job_id, key

    raise ValueError("Cần --key hoặc --job-id (hoặc --poll)")


def load_manifest_stats(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    stats: dict[str, Any] = {
        "success_count": len(data.get("success_documents", [])),
        "tentative_count": len(data.get("tentative_documents", [])),
        "orphan_count": len(data.get("orphan_pages", [])),
    }
    validation = data.get("validation") or {}
    if "completeness_pct" in validation:
        stats["completeness_pct"] = validation.get("completeness_pct")
    if "orphan_rate_pct" in data:
        stats["orphan_rate_pct"] = data.get("orphan_rate_pct")
    return stats


def process_job(store: MinioStore, args: argparse.Namespace, input_key: str, job_id: str) -> int:
    from minio_store import _utc_now_iso, sanitize_job_id

    job_id = sanitize_job_id(job_id) or job_id

    output_prefix = store.output_prefix_for_job(job_id)
    started_at = _utc_now_iso()
    pipeline_flags = pipeline_flags_from_args(args)

    store.write_status(
        job_id,
        store.build_status(
            job_id,
            status="running",
            input_key=input_key,
            output_prefix=output_prefix,
            started_at=started_at,
            pipeline_args=pipeline_flags,
        ),
    )

    work_dir = store.job_work_dir(job_id)
    local_input = work_dir / "input.pdf"
    local_output = work_dir / "output"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    local_output.mkdir(parents=True, exist_ok=True)

    try:
        store.download_object(input_key, local_input)
        run_argv = build_main_argv(args, local_input, local_output)
        logger.info(f"[job {job_id}] pipeline: {' '.join(run_argv[2:])}")

        import threading

        stop_sync = threading.Event()

        def _sync_progress():
            progress_path = local_output / "progress.json"
            last_pct = -1.0
            while not stop_sync.wait(2.0):
                try:
                    if progress_path.is_file():
                        p_data = json.loads(progress_path.read_text(encoding="utf-8"))
                        pct = p_data.get("percent", 0.0)
                        if pct != last_pct:
                            last_pct = pct
                            store.write_status(
                                job_id,
                                store.build_status(
                                    job_id,
                                    status="running",
                                    input_key=input_key,
                                    output_prefix=output_prefix,
                                    progress=p_data,
                                    started_at=started_at,
                                    pipeline_args=pipeline_flags,
                                ),
                            )
                except Exception:
                    pass

        sync_thread = threading.Thread(target=_sync_progress, daemon=True)
        sync_thread.start()

        try:
            proc = subprocess.run(run_argv, cwd=str(config.PROJECT_ROOT), check=False)
        finally:
            stop_sync.set()
            sync_thread.join(timeout=1.0)

        if proc.returncode != 0:
            raise RuntimeError(f"main.py exit code {proc.returncode}")

        uploaded = store.upload_directory(local_output, output_prefix)
        stats = load_manifest_stats(local_output)

        archived_key = None
        if getattr(args, "archive", False):
            archived_key = store.archive_inbox_object(input_key, job_id)

        final_progress = {
            "status": "completed",
            "percent": 100.0,
            "stage": "Hoàn thành",
            "updated_at": _utc_now_iso(),
        }
        if (local_output / "progress.json").is_file():
            try:
                final_progress = json.loads((local_output / "progress.json").read_text(encoding="utf-8"))
            except Exception:
                pass

        store.write_status(
            job_id,
            store.build_status(
                job_id,
                status="completed",
                input_key=input_key,
                output_prefix=output_prefix,
                manifest_key=None,
                stats=stats,
                progress=final_progress,
                pipeline_args=pipeline_flags,
                started_at=started_at,
                finished_at=_utc_now_iso(),
                archived_key=archived_key,
            ),
        )
        logger.info(
            f"[job {job_id}] completed — output s3://{store.settings.bucket}/{output_prefix}/ "
            f"({len(uploaded)} files)"
        )
        return 0

    except Exception as exc:
        logger.exception(f"[job {job_id}] failed: {exc}")
        store.write_status(
            job_id,
            store.build_status(
                job_id,
                status="failed",
                input_key=input_key,
                output_prefix=output_prefix,
                error=str(exc),
                pipeline_args=pipeline_flags,
                started_at=started_at,
                finished_at=_utc_now_iso(),
            ),
        )
        return 1

    finally:
        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    args = parse_args()
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.debug else "INFO")

    try:
        settings = MinioSettings.from_env()
        store = MinioStore(settings)
    except Exception as exc:
        logger.error(f"MinIO config: {exc}")
        return 1

    store.ensure_bucket()

    if args.setup and not args.poll and not args.key and not args.job_id:
        logger.info(
            f"Bucket sẵn sàng: s3://{settings.bucket} "
            f"(inbox={settings.prefix_inbox}/, output={settings.prefix_output}/)"
        )
        return 0

    if args.poll:
        keys = store.list_inbox_pdfs(args.inbox_prefix or "")
        if not keys:
            logger.info("Inbox trống — không có PDF để xử lý.")
            return 0
        logger.info(f"Poll inbox: {len(keys)} PDF")
        rc = 0
        for key in keys:
            job_id = store.derive_job_id(key)
            if not getattr(args, "force", False):
                st = store.read_status(job_id)
                if st and st.get("status") == "completed":
                    logger.debug(f"[job {job_id}] Đã hoàn thành (status=completed) -> Bỏ qua.")
                    continue
            rc = process_job(store, args, key, job_id) or rc
        return rc

    try:
        job_id, input_key = resolve_input_key(store, args)
    except (ValueError, FileNotFoundError) as exc:
        logger.error(str(exc))
        return 1

    return process_job(store, args, input_key, job_id)


if __name__ == "__main__":
    sys.exit(main())
