"""
pipeline/review_namer.py
========================
Đặt tên file review/orphan theo tín hiệu nghiệp vụ (biên bản, mục lục…)
thay vì chỉ ORPHAN_page_XXXX.
"""

from __future__ import annotations

from typing import Any, Optional

from unidecode import unidecode

from pipeline.doc_identity import looks_like_standalone_minutes


def orphan_review_stem(page_num: int, signal: Optional[Any] = None) -> str:
    """
    Trả về stem (không đuôi .pdf) cho trang orphan/review.

    Ưu tiên:
      MUC_LUC_page_XXXX
      BIEN_BAN_page_XXXX
      PHU_LUC_page_XXXX
      ORPHAN_page_XXXX
    """
    suffix = f"page_{page_num:04d}"
    if signal is None:
        return f"ORPHAN_{suffix}"

    if getattr(signal, "is_toc", False):
        return f"MUC_LUC_{suffix}"

    kind = (getattr(signal, "appendix_kind", "") or "").upper()
    header = getattr(signal, "header_text", "") or ""
    full = getattr(signal, "full_text", "") or ""

    if kind == "PHU_LUC_NGHI_QUYET" or looks_like_standalone_minutes(header, full):
        return f"BIEN_BAN_{suffix}"

    if kind.startswith("PHU_LUC") or getattr(signal, "is_appendix", False):
        return f"PHU_LUC_{suffix}"

    blob = unidecode(header + "\n" + full[:400]).lower()
    if "muc luc" in blob and "tai lieu" in blob:
        return f"MUC_LUC_{suffix}"

    return f"ORPHAN_{suffix}"


def orphan_review_filename(page_num: int, signal: Optional[Any] = None) -> str:
    return f"{orphan_review_stem(page_num, signal)}.pdf"
