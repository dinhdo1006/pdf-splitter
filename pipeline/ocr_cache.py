"""
Cache tín hiệu trang (sau OCR) để vòng lặp test→sửa→test không OCR lại.
Khi load: re-run matcher trên text đã cache để nhận logic mới.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from pipeline.party_doc_matcher import get_matcher
from pipeline.signal_extractor import PageSignal


_CACHE_FIELDS = (
    "page_num",
    "header_text",
    "full_text",
    "has_large_centered_text",
    "is_continuation",
    "text_density",
    "is_blank",
    "avg_confidence",
    "page_width_pt",
    "page_height_pt",
    "page_size_group",
    "ocr_dpi_recommended",
    "is_likely_end_of_doc",
    "end_of_doc_confidence",
)


def cache_dir_for(output_dir: Path, dpi: int, preprocess: bool) -> Path:
    tag = f"dpi{dpi}_{'prep' if preprocess else 'noprep'}"
    return Path(output_dir) / "_ocr_cache" / tag


def save_signal(cache_dir: Path, signal: PageSignal) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"page_{signal.page_num:04d}.json"
    payload = {k: getattr(signal, k, None) for k in _CACHE_FIELDS}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def refresh_match_fields(signal: PageSignal) -> PageSignal:
    """Áp matcher mới lên text đã OCR (không cần ảnh)."""
    matcher = get_matcher()
    result = matcher.match(
        signal.header_text,
        signal.full_text,
        page_size_group=signal.page_size_group or "OTHER",
    )
    signal.matched_doc_type = result.doc_type_key or ""
    signal.matched_keyword = result.matched_phrase or ""
    signal.match_score = float(result.score or 0.0)
    signal.has_doc_keyword = bool(result.doc_type_key)
    signal.is_toc = result.source == "toc"
    signal.is_form_section = result.source == "form_section"
    signal.is_appendix = result.source == "appendix"
    signal.appendix_kind = (
        result.matched_phrase if result.source == "appendix" else ""
    )
    return signal


def load_signal(cache_dir: Path, page_num: int) -> Optional[PageSignal]:
    path = cache_dir / f"page_{page_num:04d}.json"
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[ocr_cache] load fail page {page_num}: {exc}")
        return None
    signal = PageSignal(
        page_num=int(data.get("page_num", page_num)),
        header_text=data.get("header_text") or "",
        header_blocks=[],
        full_text=data.get("full_text") or "",
        has_doc_keyword=False,
        matched_keyword="",
        has_large_centered_text=bool(data.get("has_large_centered_text")),
        is_continuation=bool(data.get("is_continuation")),
        text_density=float(data.get("text_density") or 0.0),
        is_blank=bool(data.get("is_blank")),
        avg_confidence=float(data.get("avg_confidence") or 0.0),
        matched_doc_type="",
        match_score=0.0,
        all_blocks=[],
        is_toc=False,
        is_form_section=False,
        page_width_pt=float(data.get("page_width_pt") or 0.0),
        page_height_pt=float(data.get("page_height_pt") or 0.0),
        page_size_group=data.get("page_size_group") or "OTHER",
        ocr_dpi_recommended=int(data.get("ocr_dpi_recommended") or 200),
        boundary_score=0.0,
        is_likely_end_of_doc=bool(data.get("is_likely_end_of_doc")),
        end_of_doc_confidence=float(data.get("end_of_doc_confidence") or 0.0),
        is_appendix=False,
        appendix_kind="",
    )
    return refresh_match_fields(signal)


def cache_coverage(cache_dir: Path, n_pages: int) -> int:
    if not cache_dir.exists():
        return 0
    return sum(
        1 for i in range(1, n_pages + 1) if (cache_dir / f"page_{i:04d}.json").exists()
    )
