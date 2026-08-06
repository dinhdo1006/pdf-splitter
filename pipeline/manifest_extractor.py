"""
pipeline/manifest_extractor.py
==============================
Phát hiện trang Mục Lục → extract danh sách loại tài liệu trong hồ sơ.
Dùng để validate output (missing/extra), không tự sửa ranh giới.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from loguru import logger
from rapidfuzz import fuzz
from unidecode import unidecode

from pipeline.party_catalog import PARTY_DOC_CATALOG
from pipeline.party_doc_matcher import is_table_of_contents


@dataclass
class ManifestEntry:
    raw_text: str
    normalized_text: str
    catalog_key: Optional[str]
    catalog_stt: Optional[str]
    is_present: bool
    page_count_hint: Optional[int] = None


@dataclass
class HoSoManifest:
    source_page: int
    entries: list[ManifestEntry] = field(default_factory=list)
    party_member_name: Optional[str] = None
    extraction_confidence: float = 0.0


_STT_LINE_RE = re.compile(
    r"^\s*(\d{1,3})[\.\)\-:]\s*(.+)$"
)


def extract_manifest(
    page_num: int,
    ocr_text: str,
    header_text: str = "",
) -> Optional[HoSoManifest]:
    """Trả về HoSoManifest nếu trang là mục lục và parse được entries."""
    try:
        if not is_table_of_contents(header_text or ocr_text, ocr_text):
            return None

        manifest = HoSoManifest(source_page=page_num)

        name_match = re.search(
            r"(?:ho\s+(?:va\s+)?ten|dong\s+chi)[:\s]+([A-Za-zÀ-ỹ\s]{5,40})",
            ocr_text or "",
            re.IGNORECASE,
        )
        if name_match:
            manifest.party_member_name = name_match.group(1).strip().title()

        seen_keys: set[str] = set()
        lines = [ln.strip() for ln in (ocr_text or "").splitlines() if ln.strip()]
        for line in lines:
            if len(line) < 8:
                continue

            line_ascii = unidecode(line)
            is_present = bool(re.search(r"\bC[oó]\b", line, re.IGNORECASE)) or bool(
                re.search(r"\bCo\b", line_ascii, re.IGNORECASE)
            )
            is_absent = bool(re.search(r"\bKh[oô]ng\b", line, re.IGNORECASE)) or bool(
                re.search(r"\bKhong\b", line_ascii, re.IGNORECASE)
            )

            clean_line = re.sub(
                r"\b(C[oó]|Kh[oô]ng|Co|Khong)\b",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            # Bỏ STT đầu dòng "01. Ly lich..."
            m_stt = _STT_LINE_RE.match(unidecode(clean_line))
            if m_stt:
                clean_line = m_stt.group(2).strip()

            # Bỏ qua header bảng
            low = unidecode(clean_line).lower()
            if any(
                h in low
                for h in (
                    "ten tai lieu",
                    "muc luc",
                    "ly do khong",
                    "so thu tu",
                    "ghi chu",
                )
            ):
                continue

            best_key, best_score = _fuzzy_match_catalog(clean_line)

            # Có/Không rõ → lấy; không có thì vẫn lấy nếu match catalog mạnh (OCR mất cột)
            if is_present or is_absent:
                if best_score >= 55 and best_key and best_key not in seen_keys:
                    seen_keys.add(best_key)
                    manifest.entries.append(
                        ManifestEntry(
                            raw_text=line,
                            normalized_text=clean_line,
                            catalog_key=best_key,
                            catalog_stt=PARTY_DOC_CATALOG[best_key][0],
                            is_present=is_present,
                        )
                    )
            elif best_score >= 72 and best_key and best_key not in seen_keys:
                seen_keys.add(best_key)
                manifest.entries.append(
                    ManifestEntry(
                        raw_text=line,
                        normalized_text=clean_line,
                        catalog_key=best_key,
                        catalog_stt=PARTY_DOC_CATALOG[best_key][0],
                        is_present=True,  # optimistic khi OCR mất cột Có/Không
                    )
                )

        # Fallback: fuzzy toàn trang với từng tên catalog (khi OCR dính dòng)
        if len(manifest.entries) < 3:
            blob = unidecode(ocr_text or "").lower()
            for key, (stt, ten, _prio) in PARTY_DOC_CATALOG.items():
                if key in seen_keys:
                    continue
                ten_n = unidecode(ten).lower()
                if len(ten_n) < 14:
                    continue
                if ten_n in blob or fuzz.partial_ratio(ten_n, blob) >= 90:
                    seen_keys.add(key)
                    manifest.entries.append(
                        ManifestEntry(
                            raw_text=ten,
                            normalized_text=ten,
                            catalog_key=key,
                            catalog_stt=stt,
                            is_present=True,
                        )
                    )

        manifest.extraction_confidence = min(1.0, len(manifest.entries) / 8.0)
        if manifest.extraction_confidence < 0.3 and not manifest.entries:
            manifest.extraction_confidence = 0.35
        logger.info(
            f"[manifest] page {page_num}: {len(manifest.entries)} entries, "
            f"conf={manifest.extraction_confidence:.2f}"
        )
        return manifest
    except Exception as exc:
        logger.warning(f"[manifest] extract failed page {page_num}: {exc}")
        return None


def _fuzzy_match_catalog(text: str) -> tuple[Optional[str], float]:
    best_key: Optional[str] = None
    best_score = 0.0
    text_norm = unidecode(text or "").lower()
    if not text_norm.strip():
        return None, 0.0
    for key, (_stt, ten, _prio) in PARTY_DOC_CATALOG.items():
        score = float(fuzz.partial_ratio(text_norm, unidecode(ten).lower()))
        if score > best_score:
            best_score = score
            best_key = key
    return best_key, best_score


def validate_output_vs_manifest(
    manifest: HoSoManifest,
    exported_doc_types: list[str],
) -> dict[str, Any]:
    """So sánh manifest với các doc_type đã export."""
    expected_present = {
        e.catalog_key
        for e in manifest.entries
        if e.is_present and e.catalog_key
    }
    exported_types = {t.upper() for t in exported_doc_types if t}

    missing = expected_present - exported_types
    extra = exported_types - expected_present
    matched = expected_present & exported_types

    return {
        "manifest_source_page": manifest.source_page,
        "party_member_name": manifest.party_member_name,
        "expected_count": len(expected_present),
        "exported_count": len(exported_types),
        "matched": sorted(matched),
        "missing_in_output": sorted(missing),
        "extra_in_output": sorted(extra),
        "completeness_pct": round(
            len(matched) / max(len(expected_present), 1) * 100, 1
        ),
        "entry_count": len(manifest.entries),
        "extraction_confidence": manifest.extraction_confidence,
    }


def manifest_to_dict(manifest: HoSoManifest) -> dict[str, Any]:
    return {
        "source_page": manifest.source_page,
        "party_member_name": manifest.party_member_name,
        "extraction_confidence": manifest.extraction_confidence,
        "entries": [asdict(e) for e in manifest.entries],
    }
