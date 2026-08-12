"""MinIO client — inbox / output / job status cho luồng PDF splitter."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

try:
    from minio import Minio
    from minio.commonconfig import CopySource
    from minio.error import S3Error
except ImportError as exc:
    Minio = None  # type: ignore[misc, assignment]
    CopySource = None  # type: ignore[misc, assignment]
    S3Error = Exception  # type: ignore[misc, assignment]
    _MINIO_IMPORT_ERROR = exc
else:
    _MINIO_IMPORT_ERROR = None

import config

_SKIP_UPLOAD_PARTS = frozenset({"_ocr_cache", "__pycache__"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    return p.strip("/")


def _join_key(*parts: str) -> str:
    cleaned = [_norm_prefix(p) for p in parts if p and _norm_prefix(p)]
    return "/".join(cleaned)


@dataclass
class MinioSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = "hsdv-pdf-splitter"
    secure: bool = False
    auto_create_bucket: bool = True
    prefix_inbox: str = "inbox"
    prefix_output: str = "output"
    prefix_archive: str = "archive/inbox"
    prefix_status: str = "jobs"
    work_dir: Path = field(default_factory=lambda: config.PROJECT_ROOT / "work_minio")

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "MinioSettings":
        try:
            from dotenv import load_dotenv
        except ImportError:
            load_dotenv = None

        if load_dotenv is not None:
            if env_path is not None:
                load_dotenv(env_path)
            else:
                load_dotenv(config.PROJECT_ROOT / ".env")

        endpoint = os.getenv("MINIO_ENDPOINT", "10.10.6.134:9000").strip()
        access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
        secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
        if not access_key or not secret_key:
            raise ValueError(
                "Thiếu MINIO_ACCESS_KEY / MINIO_SECRET_KEY trong .env "
                "(copy từ .env.example và điền key MinIO)."
            )

        work = os.getenv("MINIO_WORK_DIR", "").strip()
        work_dir = Path(work) if work else config.PROJECT_ROOT / "work_minio"

        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=os.getenv("MINIO_BUCKET", "hsdv-pdf-splitter").strip(),
            secure=os.getenv("MINIO_SECURE", "false").strip().lower() in {"1", "true", "yes"},
            auto_create_bucket=os.getenv("MINIO_AUTO_CREATE_BUCKET", "true").strip().lower()
            in {"1", "true", "yes"},
            prefix_inbox=_norm_prefix(os.getenv("MINIO_PREFIX_INBOX", "inbox")),
            prefix_output=_norm_prefix(os.getenv("MINIO_PREFIX_OUTPUT", "output")),
            prefix_archive=_norm_prefix(os.getenv("MINIO_PREFIX_ARCHIVE", "archive/inbox")),
            prefix_status=_norm_prefix(os.getenv("MINIO_PREFIX_STATUS", "jobs")),
            work_dir=work_dir,
        )


class MinioStore:
    def __init__(self, settings: MinioSettings) -> None:
        if Minio is None:
            raise ImportError(
                f"Chưa cài minio: pip install minio ({_MINIO_IMPORT_ERROR})"
            )
        self.settings = settings
        self.client = Minio(
            settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            secure=settings.secure,
        )

    def ensure_bucket(self) -> None:
        bucket = self.settings.bucket
        if self.client.bucket_exists(bucket):
            logger.info(f"[minio] bucket exists: {bucket}")
            return
        if not self.settings.auto_create_bucket:
            raise RuntimeError(f"Bucket chưa tồn tại: {bucket}")
        self.client.make_bucket(bucket)
        logger.info(f"[minio] created bucket: {bucket}")

    def inbox_key_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_inbox, f"{job_id}.pdf")

    def output_prefix_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_output, job_id)

    def status_key_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_status, job_id, "status.json")

    def archive_key_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_archive, f"{job_id}.pdf")

    def list_inbox_pdfs(self) -> list[str]:
        prefix = _join_key(self.settings.prefix_inbox) + "/"
        keys: list[str] = []
        for obj in self.client.list_objects(
            self.settings.bucket,
            prefix=prefix,
            recursive=True,
        ):
            name = obj.object_name
            if not name or not name.lower().endswith(".pdf"):
                continue
            # Bỏ qua file đã archive (nếu lỡ cùng prefix)
            if "/archive/" in name.lower():
                continue
            keys.append(name)
        keys.sort()
        return keys

    def object_exists(self, object_key: str) -> bool:
        try:
            self.client.stat_object(self.settings.bucket, object_key)
            return True
        except S3Error:
            return False

    def download_object(self, object_key: str, local_path: Path) -> Path:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(self.settings.bucket, object_key, str(local_path))
        logger.info(f"[minio] downloaded s3://{self.settings.bucket}/{object_key} → {local_path}")
        return local_path

    def upload_file(self, local_path: Path, object_key: str) -> str:
        self.client.fput_object(
            self.settings.bucket,
            object_key,
            str(local_path),
        )
        return object_key

    def upload_directory(
        self,
        local_dir: Path,
        dest_prefix: str,
        *,
        skip_parts: frozenset[str] = _SKIP_UPLOAD_PARTS,
    ) -> list[str]:
        if not local_dir.is_dir():
            raise FileNotFoundError(f"Output dir not found: {local_dir}")

        uploaded: list[str] = []
        dest_prefix = _norm_prefix(dest_prefix)
        for path in sorted(local_dir.rglob("*")):
            if not path.is_file():
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            rel = path.relative_to(local_dir).as_posix()
            key = _join_key(dest_prefix, rel)
            self.upload_file(path, key)
            uploaded.append(key)
        logger.info(f"[minio] uploaded {len(uploaded)} files → {dest_prefix}/")
        return uploaded

    def write_status(self, job_id: str, payload: dict[str, Any]) -> str:
        key = self.status_key_for_job(job_id)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        from io import BytesIO

        self.client.put_object(
            self.settings.bucket,
            key,
            BytesIO(body),
            length=len(body),
            content_type="application/json",
        )
        logger.info(f"[minio] status → s3://{self.settings.bucket}/{key}")
        return key

    def read_status(self, job_id: str) -> Optional[dict[str, Any]]:
        key = self.status_key_for_job(job_id)
        try:
            resp = self.client.get_object(self.settings.bucket, key)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return json.loads(data.decode("utf-8"))
        except S3Error:
            return None

    def archive_inbox_object(self, source_key: str, job_id: str) -> Optional[str]:
        dest_key = self.archive_key_for_job(job_id)
        try:
            self.client.copy_object(
                self.settings.bucket,
                dest_key,
                CopySource(self.settings.bucket, source_key),
            )
            self.client.remove_object(self.settings.bucket, source_key)
            logger.info(f"[minio] archived {source_key} → {dest_key}")
            return dest_key
        except S3Error as exc:
            logger.warning(f"[minio] archive failed for {source_key}: {exc}")
            return None

    def job_work_dir(self, job_id: str) -> Path:
        return self.settings.work_dir / job_id

    def derive_job_id(self, object_key: str) -> str:
        name = Path(object_key).name
        if name.lower().endswith(".pdf"):
            return Path(name).stem
        return Path(object_key).stem

    def build_status(
        self,
        job_id: str,
        *,
        status: str,
        input_key: str,
        output_prefix: str,
        manifest_key: Optional[str] = None,
        error: Optional[str] = None,
        stats: Optional[dict[str, Any]] = None,
        pipeline_args: Optional[list[str]] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        archived_key: Optional[str] = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "status": status,
            "input_key": input_key,
            "output_prefix": output_prefix + "/",
            "manifest_key": manifest_key,
            "archived_input_key": archived_key,
            "bucket": self.settings.bucket,
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error,
            "stats": stats or {},
            "pipeline_args": pipeline_args or [],
            "updated_at": _utc_now_iso(),
        }
