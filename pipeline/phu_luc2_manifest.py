"""
pipeline/phu_luc2_manifest.py
=============================
Ghi manifest_ho_so.json trong member_dir theo khung Phụ lục 2
(đồng bộ path + danh sách file đã export).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from pipeline.party_catalog import PARTY_DOC_CATALOG, fmt_stt, priority_from_stt


def write_member_manifest(
    member_dir: Path,
    *,
    export_result: dict,
    member_identity: Optional[dict] = None,
    source_pdf: str = "",
    extra: Optional[dict] = None,
) -> Path:
    """
    Tạo manifest_ho_so.json cạnh các file PDF thành công trong member_dir.
    """
    member_dir = Path(member_dir)
    member_dir.mkdir(parents=True, exist_ok=True)

    docs: list[dict[str, Any]] = []
    for bucket in ("success", "tentative", "review"):
        for r in export_result.get(bucket, []) or []:
            key = (r.get("doc_type") or "").upper()
            stt = ""
            ten = ""
            prio = 0
            if key in PARTY_DOC_CATALOG:
                stt_raw, ten, prio_cat = PARTY_DOC_CATALOG[key]
                stt = fmt_stt(stt_raw)
                prio = prio_cat or priority_from_stt(stt_raw)
            docs.append(
                {
                    "filename": r.get("filename"),
                    "doc_type_key": key or None,
                    "stt": stt or None,
                    "ten_tai_lieu": ten or None,
                    "do_uu_tien": prio or None,
                    "doc_year": r.get("doc_year"),
                    "sequence_number": r.get("sequence_number"),
                    "page_range": r.get("page_range"),
                    "page_count": r.get("page_count"),
                    "bucket": bucket,
                    "output_path": r.get("output_path"),
                }
            )

    payload = {
        "schema": "phu_luc_2_member_manifest_v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_pdf": source_pdf,
        "member_dir": str(member_dir.resolve()),
        "member_identity": member_identity or {},
        "documents": docs,
        "orphans": export_result.get("orphans", []),
        "counts": {
            "success": len(export_result.get("success", []) or []),
            "tentative": len(export_result.get("tentative", []) or []),
            "review": len(export_result.get("review", []) or []),
            "orphans": len(export_result.get("orphans", []) or []),
        },
    }
    if extra:
        payload["extra"] = extra

    out = member_dir / "manifest_ho_so.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"[phu_luc2] wrote {out}")
    return out
