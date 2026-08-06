"""
pipeline/doc_identity.py
=======================
Trích số quyết định / mã văn bản để tách ranh giới giữa các QĐ cùng loại.
"""

from __future__ import annotations

import re
from typing import Optional

from unidecode import unidecode

from pipeline.year_aware_sequencer import extract_year_robust

# SO: 123/QĐ-ĐU, So 05-QN/DB, Quyet dinh so 12/QD-...
_DECISION_RE = re.compile(
    r"(?:so|s[oô]|quyet\s*dinh\s*so?)\s*[:\.]?\s*"
    r"([0-9A-Za-z]{1,6}\s*[/\-]\s*[0-9A-Za-zĐđQq][0-9A-Za-z./\-]{1,30})",
    re.IGNORECASE,
)
_DECISION_RE2 = re.compile(
    r"\b(\d{1,4}\s*/\s*[A-ZĐ]{1,6}[-–][A-ZĐ0-9]{1,12})\b",
    re.IGNORECASE,
)

# Loại thường 1 trang / phải tách khi đổi số hoặc năm
SINGLE_PAGE_DOC_TYPES = frozenset(
    {
        "PHIEU_DANG_VIEN",
    }
)


def _norm_ref(raw: str) -> str:
    s = unidecode(raw or "").upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("–", "-").replace("—", "-")
    return s[:48]


def extract_decision_ref(text: str) -> Optional[str]:
    """Trả về mã số quyết định đã chuẩn hóa, hoặc None."""
    if not text or not text.strip():
        return None
    blob = unidecode(text[:800])
    for cre in (_DECISION_RE, _DECISION_RE2):
        m = cre.search(blob)
        if m:
            return _norm_ref(m.group(1))
    return None


def looks_like_phieu_bo_sung(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:400]).lower()
    return any(
        x in blob
        for x in (
            "phieu bo sung",
            "bo sung ho so",
            "mau 3",
            "mau 3-hsdv",
            "mau 3 hsdv",
            "mu 3 hsdv",
            "mu 3-hsdv",
        )
    )


def looks_like_phieu_dang_vien(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:300]).lower()
    if looks_like_phieu_bo_sung(header, full_text):
        return False
    return any(
        x in blob
        for x in ("phieu dang vien", "mau 2", "mu 2", "mau 2-hsdv", "mu 2-hsdv")
    )


def is_quyet_dinh_type(doc_type: str | None) -> bool:
    return bool(doc_type) and str(doc_type).upper().startswith("QUYET_DINH")


def should_force_new_document(
    open_doc_type: str | None,
    open_year: int | None,
    open_ref: str | None,
    open_page_count: int,
    curr_doc_type: str | None,
    curr_header: str,
    curr_full: str,
) -> tuple[bool, str]:
    """
    Quyết định có nên mở NEW thay vì gộp cùng loại.
    Returns (force_new, reason).
    """
    open_t = (open_doc_type or "").upper()
    curr_t = (curr_doc_type or "").upper()
    blob_year = extract_year_robust((curr_header or "") + "\n" + (curr_full or "")[:400])
    curr_ref = extract_decision_ref((curr_header or "") + "\n" + (curr_full or "")[:500])

    # Phiếu ĐV đã có ≥1 trang + trang sau là phiếu bổ sung
    if open_t == "PHIEU_DANG_VIEN" and open_page_count >= 1:
        if curr_t == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN" or looks_like_phieu_bo_sung(
            curr_header, curr_full
        ):
            return True, "phieu_dv_to_phieu_bo_sung"
        # Phiếu ĐV thường 1 trang — không gộp thêm cùng loại
        if curr_t == "PHIEU_DANG_VIEN":
            return True, "phieu_dang_vien_single_page"

    # Đang mở phiếu bổ sung + năm khác rõ
    if open_t == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN" and curr_t == open_t:
        if (
            open_year is not None
            and blob_year is not None
            and open_year != blob_year
        ):
            return True, f"phieu_bo_sung_new_year({blob_year})"

    # Quyết định: số QĐ khác hoặc đã có trang + match QĐ mới
    if is_quyet_dinh_type(open_t) and is_quyet_dinh_type(curr_t):
        if open_ref and curr_ref and open_ref != curr_ref:
            return True, f"quyet_dinh_ref_change({open_ref}->{curr_ref})"
        if open_page_count >= 1 and curr_t:
            # Mặc định 1 QĐ / 1 file khi trang sau cũng là QĐ catalog
            return True, "quyet_dinh_one_per_file"

    if open_t in SINGLE_PAGE_DOC_TYPES and open_page_count >= 1 and curr_t == open_t:
        return True, "single_page_doc_type"

    return False, ""
