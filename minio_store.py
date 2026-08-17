"""MinIO client — inbox / output / job status cho luồng PDF splitter."""

from __future__ import annotations

import json
import os
import re
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


def sanitize_job_id(raw: str) -> str:
    """
    Chuẩn hóa tên file / job_id: khoảng trắng → _, bỏ ký tự lạ.
    'Ho so Dang vien.pdf' → 'Ho_so_Dang_vien'
    """
    s = (raw or "").strip().replace("\\", "/")
    s = s.split("/")[-1]
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w.\-]", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("._-")
    return s[:128]


def _stem_from_key(object_key: str) -> str:
    name = Path(object_key.replace("\\", "/")).name
    if name.lower().endswith(".pdf"):
        return name[:-4]
    return Path(name).stem


def _parse_s3_json(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Parse S3/MinIO JSON (endPoint, port, accessKey, secretKey, bucket, useSSL)."""
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    host = str(data.get("endPoint") or data.get("endpoint") or "").strip()
    port = data.get("port")
    if host and port:
        endpoint = f"{host}:{int(port)}"
    else:
        endpoint = host or "10.10.4.21:9000"
    access_key = str(data.get("accessKey") or data.get("access_key") or "").strip()
    secret_key = str(data.get("secretKey") or data.get("secret_key") or "").strip()
    bucket = str(data.get("bucket") or "hsdv-pdf-splitter").strip()
    use_ssl = data.get("useSSL", data.get("use_ssl", False))
    secure = str(use_ssl).strip().lower() in {"1", "true", "yes"}
    return {
        "endpoint": endpoint,
        "access_key": access_key,
        "secret_key": secret_key,
        "bucket": bucket,
        "secure": secure,
    }


def _load_s3_json_config() -> Optional[dict[str, Any]]:
    config_path = os.getenv("MINIO_S3_CONFIG", "").strip()
    if config_path:
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"MINIO_S3_CONFIG không tồn tại: {path}")
        return _parse_s3_json(path.read_text(encoding="utf-8"))
    raw = os.getenv("MINIO_S3_JSON", "").strip()
    if raw:
        return _parse_s3_json(raw)
    default_json = config.PROJECT_ROOT / "minio_s3_config.json"
    if default_json.is_file():
        return _parse_s3_json(default_json.read_text(encoding="utf-8"))
    return None


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

        s3_cfg = _load_s3_json_config()
        if s3_cfg:
            endpoint = s3_cfg["endpoint"]
            access_key = s3_cfg["access_key"]
            secret_key = s3_cfg["secret_key"]
            bucket_default = s3_cfg["bucket"]
            secure_default = s3_cfg["secure"]
        else:
            endpoint = os.getenv("MINIO_ENDPOINT", "10.10.4.21:9000").strip()
            access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
            secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
            bucket_default = os.getenv("MINIO_BUCKET", "hsdv-pdf-splitter").strip()
            secure_default = os.getenv("MINIO_SECURE", "false").strip().lower() in {
                "1",
                "true",
                "yes",
            }

        if not access_key or not secret_key:
            raise ValueError(
                "Thiếu MinIO credentials. Tạo minio_s3_config.json (copy từ example) "
                "hoặc điền MINIO_ACCESS_KEY / MINIO_SECRET_KEY trong .env."
            )

        work = os.getenv("MINIO_WORK_DIR", "").strip()
        work_dir = Path(work) if work else config.PROJECT_ROOT / "work_minio"

        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=os.getenv("MINIO_BUCKET", bucket_default).strip(),
            secure=os.getenv("MINIO_SECURE", str(secure_default)).strip().lower()
            in {"1", "true", "yes"},
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
        return _join_key(self.settings.prefix_output, sanitize_job_id(job_id) or job_id)

    def status_key_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_status, sanitize_job_id(job_id) or job_id, "status.json")

    def archive_key_for_job(self, job_id: str) -> str:
        return _join_key(self.settings.prefix_archive, f"{sanitize_job_id(job_id) or job_id}.pdf")

    def list_inbox_pdfs(self, subprefix: str = "") -> list[str]:
        prefix = _join_key(self.settings.prefix_inbox, subprefix)
        if prefix:
            prefix = prefix + "/"
        keys: list[str] = []
        for obj in self.client.list_objects(
            self.settings.bucket,
            prefix=prefix,
            recursive=True,
        ):
            name = obj.object_name
            if not name or not name.lower().endswith(".pdf"):
                continue
            if "/archive/" in name.lower():
                continue
            keys.append(name)
        keys.sort()
        return keys

    def resolve_inbox_pdf(self, hint: str) -> tuple[str, str]:
        """
        Tìm PDF inbox từ job_id hoặc tên file (có khoảng trắng / dấu).
        Returns (job_id_sanitized, object_key).
        """
        hint = (hint or "").strip().replace("\\", "/")
        if not hint:
            raise FileNotFoundError("job_id / tên file trống")

        # Key đầy đủ
        if hint.lower().endswith(".pdf"):
            key = hint if hint.startswith(self.settings.prefix_inbox) else _join_key(
                self.settings.prefix_inbox, hint
            )
            if self.object_exists(key):
                return sanitize_job_id(hint) or sanitize_job_id(key), key

        sanitized = sanitize_job_id(hint)
        if not sanitized:
            raise FileNotFoundError(f"Không chuẩn hóa được job_id từ: {hint!r}")

        candidates = [
            _join_key(self.settings.prefix_inbox, f"{hint}.pdf")
            if not hint.lower().endswith(".pdf")
            else _join_key(self.settings.prefix_inbox, hint),
            self.inbox_key_for_job(sanitized),
            _join_key(self.settings.prefix_inbox, f"{hint.strip()}.pdf"),
        ]
        seen: set[str] = set()
        for key in candidates:
            if not key or key in seen:
                continue
            seen.add(key)
            if self.object_exists(key):
                return sanitized, key

        want = sanitized.lower()
        for key in self.list_inbox_pdfs():
            if sanitize_job_id(key).lower() == want:
                return sanitized, key

        raise FileNotFoundError(
            f"Không thấy PDF inbox khớp {hint!r} "
            f"(đã thử tên gốc và tên bỏ khoảng trắng: {sanitized}.pdf)"
        )

    def derive_job_id(self, object_key: str) -> str:
        return sanitize_job_id(_stem_from_key(object_key)) or sanitize_job_id(object_key)

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
            if path.suffix.lower() == ".json":
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
        for candidate in (job_id, sanitize_job_id(job_id)):
            if not candidate:
                continue
            key = self.status_key_for_job(candidate)
            try:
                resp = self.client.get_object(self.settings.bucket, key)
                data = resp.read()
                resp.close()
                resp.release_conn()
                return json.loads(data.decode("utf-8"))
            except S3Error:
                continue
        return None

    def archive_inbox_object(self, source_key: str, job_id: str) -> Optional[str]:
        dest_key = self.archive_key_for_job(sanitize_job_id(job_id) or job_id)
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
        return self.settings.work_dir / (sanitize_job_id(job_id) or job_id)

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
        progress: Optional[dict[str, Any]] = None,
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
            "progress": progress or {},
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error,
            "stats": stats or {},
            "pipeline_args": pipeline_args or [],
            "updated_at": _utc_now_iso(),
        }
