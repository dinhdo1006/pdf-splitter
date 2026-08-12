"""Tạo .env từ minio_s3_config.json (file local, không commit)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "minio_s3_config.json"
DST = ROOT / ".env"


def main() -> int:
    if not SRC.is_file():
        print(f"Không thấy {SRC}")
        print("cp minio_s3_config.example.json minio_s3_config.json")
        return 1

    data = json.loads(SRC.read_text(encoding="utf-8"))
    host = str(data.get("endPoint") or data.get("endpoint") or "10.10.6.134")
    port = int(data.get("port") or 9000)
    access = str(data.get("accessKey") or "")
    secret = str(data.get("secretKey") or "")
    bucket = str(data.get("bucket") or "hsdv-pdf-splitter")
    use_ssl = data.get("useSSL", False)

    if not access or not secret:
        print("minio_s3_config.json thiếu accessKey / secretKey")
        return 1

    lines = [
        f"MINIO_ENDPOINT={host}:{port}",
        f"MINIO_ACCESS_KEY={access}",
        f"MINIO_SECRET_KEY={secret}",
        f"MINIO_BUCKET={bucket}",
        f"MINIO_SECURE={'true' if use_ssl else 'false'}",
        "MINIO_AUTO_CREATE_BUCKET=true",
        "MINIO_PREFIX_INBOX=inbox",
        "MINIO_PREFIX_OUTPUT=output",
        "MINIO_PREFIX_ARCHIVE=archive/inbox",
        "MINIO_PREFIX_STATUS=jobs",
        "MINIO_WORK_DIR=./work_minio",
    ]
    DST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
