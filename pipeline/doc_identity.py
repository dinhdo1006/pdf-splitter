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


def looks_like_standalone_minutes(header: str, full_text: str = "") -> bool:
    """
    Biên bản / trích biên bản / họp chi bộ|chi đoàn — độc lập với Quyết định.
    Dùng unidecode + hint rộng vì OCR viết tay thường méo.
    """
    blob = unidecode((header or "") + "\n" + (full_text or "")[:900]).lower()
    strong = (
        "bien ban",
        "trich bien ban",
        "trich bb",
        "hop chi bo",
        "hop chi doan",
        "hop to dang",
        "xet chuyen dang chinh thuc",
        "xet chuyen dang vien chinh thuc",
        "chuyen dang chinh thuc",
    )
    if any(h in blob for h in strong):
        return True
    # OCR méo: "trich" + "bien" tách, hoặc Phần 1/2 + biểu quyết
    soft_pair = (
        ("trich", "bien"),
        ("hop", "chi bo"),
        ("hop", "chi doan"),
        ("phan 1", "phan 2"),
        ("bieu quyet", "100"),
        ("y kien 1", "y kien 2"),
    )
    return any(a in blob and b in blob for a, b in soft_pair)


def looks_like_quyet_dinh_or_nghi_quyet(header: str, full_text: str = "") -> bool:
    """Quyết định / nghị quyết công nhận — không thuộc phiếu ĐV."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:500]).lower()
    hints = (
        "quyet dinh",
        "nghi quyet",
        "chuan y cong nhan",
        "cong nhan dang vien chinh thuc",
        "cong nhan chinh thuc",
        "ve viec chuan y",
        "qd/tu",
        "qd-tu",
        "qn/db",
        "qn-db",
    )
    return any(h in blob for h in hints)


def looks_like_kiem_diem_header(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:350]).lower()
    return any(
        x in blob
        for x in (
            "ban tu kiem diem",
            "tu kiem diem hang nam",
            "kiem diem cuoi nam",
            "tu danh gia kiem diem",
            "ban tu danh gia",
        )
    )


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
        if looks_like_standalone_minutes(curr_header, curr_full):
            return True, "phieu_dv_to_minutes"
        if looks_like_quyet_dinh_or_nghi_quyet(curr_header, curr_full):
            return True, "phieu_dv_to_quyet_dinh"
        # Phiếu ĐV thường 2–4 trang; quá dài + header catalog mới → tách
        if open_page_count >= 4 and (
            looks_like_phieu_dang_vien(curr_header, curr_full) or curr_t == open_t
        ):
            return True, "phieu_dv_max_pages"

    # Đang mở phiếu bổ sung + năm khác rõ, hoặc header form mới sau khi đã dài
    if open_t == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN":
        if curr_t == open_t or looks_like_phieu_bo_sung(curr_header, curr_full):
            if (
                open_year is not None
                and blob_year is not None
                and open_year != blob_year
            ):
                return True, f"phieu_bo_sung_new_year({blob_year})"
            # Sau ≥5 trang mà lại thấy tiêu đề Mẫu 3 / Phiếu bổ sung → form mới
            if open_page_count >= 5 and looks_like_phieu_bo_sung(
                curr_header, curr_full
            ):
                return True, "phieu_bo_sung_new_form_header"
        if looks_like_kiem_diem_header(curr_header, curr_full):
            return True, "phieu_bo_sung_to_kiem_diem"

    # Bản tự kiểm điểm: năm khác hoặc chuyển sang phiếu bổ sung / form kiểm điểm mới
    if open_t.startswith("BAN_TU_KIEM"):
        if curr_t == open_t or looks_like_kiem_diem_header(curr_header, curr_full):
            if (
                open_year is not None
                and blob_year is not None
                and open_year != blob_year
            ):
                return True, f"kiem_diem_new_year({blob_year})"
            if open_page_count >= 6 and looks_like_kiem_diem_header(
                curr_header, curr_full
            ):
                return True, "kiem_diem_new_form_header"
        if looks_like_phieu_bo_sung(curr_header, curr_full):
            return True, "kiem_diem_to_phieu_bo_sung"

    # Quyết định: số QĐ khác hoặc đã có trang + match QĐ mới
    if is_quyet_dinh_type(open_t) and is_quyet_dinh_type(curr_t):
        if open_ref and curr_ref and open_ref != curr_ref:
            return True, f"quyet_dinh_ref_change({open_ref}->{curr_ref})"
        if open_page_count >= 1 and curr_t:
            # Mặc định 1 QĐ / 1 file khi trang sau cũng là QĐ catalog
            return True, "quyet_dinh_one_per_file"

    return False, ""
