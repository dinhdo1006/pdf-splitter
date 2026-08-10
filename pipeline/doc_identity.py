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


def looks_like_ke_khai_tai_san(header: str, full_text: str = "") -> bool:
    """Bản kê khai tài sản / thu nhập — thường đi cùng phiếu, không thuộc kiểm điểm."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:350]).lower()
    return any(
        x in blob
        for x in (
            "ban ke khai tai san",
            "ke khai tai san",
            "ke khai tai sn",
            "tai san, thu nhap",
            "tai san thu nhap",
            "giai trinh su bien dong",
            "giai trinh bien dong",
            "nguoi ke khai tai",
        )
    )


def looks_like_phieu_bo_sung(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:400]).lower()
    if looks_like_ke_khai_tai_san(header, full_text):
        return True
    return any(
        x in blob
        for x in (
            "phieu bo sung",
            "bo sung ho so",
            "mau 3",
            "mau 3-hsdv",
            "mau 3 hsdv",
            "mau 3a",
            "mu 3 hsdv",
            "mu 3-hsdv",
            "mu 3 - hsdv",
            "mu 3a",
        )
    )


def looks_like_phieu_dang_vien(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:500]).lower()
    if looks_like_phieu_bo_sung(header, full_text):
        return False
    if any(
        x in blob
        for x in (
            "phieu dang vien",
            "mau 2-hsdv",
            "mau 2 hsdv",
            "mu 2-hsdv",
            "mu 2 hsdv",
            "mau 2 - hsdv",
            "mu 2 -",
            "mau 2 -",
            "mau 2-",
            "mu 2-",
        )
    ):
        return True
    # OCR méo: "MU 2 - SV" / "PHIEU" + "DANG VIEN" trên cùng trang
    if ("mau 2" in blob or "mu 2" in blob) and (
        "phieu" in blob or "dang vien" in blob or "so tdv" in blob or "so the" in blob
    ):
        return True
    return False


def looks_like_kiem_diem_header(header: str, full_text: str = "") -> bool:
    # Họp chi đoàn/chi bộ xét kiểm điểm ≠ bản tự kiểm điểm cá nhân
    if looks_like_standalone_minutes(header, full_text):
        return False
    blob = unidecode((header or "") + "\n" + (full_text or "")[:400]).lower()
    # OCR dính chữ: "BANKIEMDIEM" / "BAN TU KIEN AIEN"
    compact = re.sub(r"[\s\-_\.]+", "", blob)
    if any(
        x in compact
        for x in (
            "bankiemdiem",
            "bantukiemdiem",
            "bantukienaien",  # OCR méo "ban tu kiem diem"
            "bankiemdiemdangvien",
        )
    ):
        return True
    return any(
        x in blob
        for x in (
            "ban tu kiem diem",
            "ban tu kien aien",  # OCR méo
            "tu kiem diem hang nam",
            "kiem diem cuoi nam",
            "tu danh gia kiem diem",
            "ban tu danh gia",
            "ban kiem diem dang vien",
            "kiem diem dang vien nam",
            "ban kiem diem",
            "ban kiemdiem",
            "nguoi kiem diem",
            "nguiri viet kiemdtiem",  # OCR méo cuối bản
            "xep loai dang vien",
            "xp loai dang vien",
            "kiem diem danh gia",
            "kiem diem dang vie",  # cắt đuôi
        )
    )


def looks_like_phieu_xin_y_kien(header: str, full_text: str = "") -> bool:
    """Phiếu xin ý kiến chi ủy / nơi cư trú — tách khỏi kiểm điểm & QĐ."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:400]).lower()
    if "phieu xin y kien" in blob or "phieu xin ykien" in blob:
        return True
    compact = re.sub(r"[\s\-_\.]+", "", blob)
    return "phieuxinykien" in compact


def looks_like_ban_giao_listing(header: str, full_text: str = "") -> bool:
    """Trang bàn giao / liệt kê tài liệu hồ sơ (không phải form catalog)."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:500]).lower()
    compact = re.sub(r"[\s\-_\.']+", "", blob)
    hints = (
        "quyen ly lich",
        "1 quyen ly lich",
        "bien ban hop chi bo",
        "giay chuyen sinh hoat",
        "phieu dang vien",
        "ban tu kiem",
    )
    hits = sum(1 for h in hints if h in blob)
    if hits >= 2:
        return True
    if ("gim:" in blob or "giao" in blob) and "ly lich" in blob and (
        "bien ban" in blob or "phieu" in blob
    ):
        return True
    # OCR méo: "HiSi oni Do Phm Hui Loit gim:" = Hồ sơ của Đ/c ... gồm:
    if ("gim:" in blob or "gom:" in blob or "gom " in blob) and (
        "lylich" in compact or "quyenly" in compact or "bienbn" in compact
    ):
        return True
    if "hoso" in compact and ("gom" in compact or "gim" in compact) and hits >= 1:
        return True
    return False


def looks_like_noi_cu_tru_form_section(header: str, full_text: str = "") -> bool:
    """
    Biên bản / ý kiến nhận xét nơi cư trú — thường là Mục IV trong phiếu bổ sung,
    không phải biên bản họp độc lập.
    """
    blob = unidecode((header or "") + "\n" + (full_text or "")[:700]).lower()
    noi = (
        "noi cu tru" in blob
        or "noi cua" in blob
        or "noi  cua" in blob
        or "noi cuá" in blob
    )
    if not noi:
        return False
    return any(
        x in blob
        for x in (
            "bien ban",
            "y kien nhan xet",
            "y kin nhan xet",
            "muc iv",
            "mu iv",
            "to truong dan pho",
            "truong dan pho",
            "to dan pho",
        )
    )


def looks_like_van_bang_chung_chi(header: str, full_text: str = "") -> bool:
    """Văn bằng / chứng chỉ tốt nghiệp / chứng thực (không phải LLCT)."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:600]).lower()
    if any(
        x in blob
        for x in (
            "ly luan chinh tri",
            "chung chi ly luan",
            "bang ly luan",
            "llct",
        )
    ):
        return False
    strong = (
        "bang tot nghiep",
        "giay chung nhan tot nghiep",
        "chung nhan tot nghiep",
        "tot nghiep",
        "so hieu bang",
        "chung thuc",
        "van bang",
    )
    if any(x in blob for x in strong):
        # Trang chứng thực UBND / bằng — tránh dính biên bản có chữ tốt
        if "bien ban" in blob and "tot nghiep" not in blob:
            return False
        return True
    return False


def looks_like_danh_gia_phan_loai(header: str, full_text: str = "") -> bool:
    """Ý kiến / biểu quyết chi bộ đánh giá phân loại đảng viên hàng năm."""
    blob = unidecode((header or "") + "\n" + (full_text or "")[:500]).lower()
    if "phan loai dang vien" in blob or "danh gia phan loai" in blob:
        return True
    if "ket qua bieu quyet" in blob and (
        "chi bo" in blob or "y kien nhan xet" in blob or "y kin nhan xet" in blob
    ):
        return True
    return False


def looks_like_qd_body_continuation(header: str, full_text: str = "") -> bool:
    """
    Trang giữa/cuối Quyết định (Điều 2/3, danh sách đồng chí) — không phải QĐ mới.
    """
    blob = unidecode((header or "") + "\n" + (full_text or "")[:700]).lower()
    head = unidecode((header or "")[:220]).lower()
    # QĐ mới thường mở bằng số + QD/TU hoặc tiêu đề QUYẾT ĐỊNH V/V
    if re.search(r"\bso[:\s].{0,12}qd", head) and "quyet dinh" in head:
        return False
    if "quyet dinh" in head and (
        "v/v" in head or "ve viec" in head or "v vic" in head
    ):
        return False
    if any(
        x in blob
        for x in (
            "dieu 2:",
            "dieu 2 ",
            "diu 2:",
            "dieu 3:",
            "dieu 3 ",
            "diu 3:",
            "chuan y ban thuong vu",
            "chun y ban thuong v",
        )
    ):
        return True
    # Danh sách thành viên BCH (nhiều "đồng chí" + chức vụ, không tiêu đề QĐ)
    if blob.count("dong chi") >= 4 and "quyet dinh" not in head:
        return True
    return False


def is_quyet_dinh_type(doc_type: str | None) -> bool:
    t = (doc_type or "").upper()
    return bool(t) and (
        t.startswith("QUYET_DINH") or t.startswith("CAC_QUYET_DINH")
    )


def looks_like_standalone_minutes(header: str, full_text: str = "") -> bool:
    """
    Biên bản / trích biên bản / họp chi bộ|chi đoàn — độc lập với Quyết định.
    Dùng unidecode + hint rộng vì OCR viết tay thường méo.
    """
    if looks_like_noi_cu_tru_form_section(header, full_text):
        return False
    blob = unidecode((header or "") + "\n" + (full_text or "")[:900]).lower()
    compact = re.sub(r"[\s\-_\.]+", "", blob)
    strong = (
        "bien ban",
        "trich bien ban",
        "trich bb",
        "hop chi bo",
        "hop chi doan",
        "hop chi-doan",
        "hop to dang",
        "xet chuyen dang chinh thuc",
        "xet chuyen dang vien chinh thuc",
        "chuyen dang chinh thuc",
    )
    if any(h in blob for h in strong):
        return True
    if "hopchidoan" in compact or "hopchi-doan" in compact:
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
        "chuan y ban thuong vu",
        "qd/tu",
        "qd-tu",
        "qn/db",
        "qn-db",
    )
    return any(h in blob for h in hints)


def looks_like_ly_lich_header(header: str, full_text: str = "") -> bool:
    blob = unidecode((header or "") + "\n" + (full_text or "")[:400]).lower()
    return any(
        x in blob
        for x in (
            "ly lich dang vien",
            "ly lich cua nguoi xin vao dang",
            "so ly lich",
            "so yeu ly lich",
            "ly lich dang",
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

    from pipeline.continuation_validator import soft_max_pages_for

    # Lý lịch: đổi loại / chạm soft-max + header LL mới / sang phiếu|kiểm điểm
    if open_t in {"LY_LICH_DANG_VIEN", "LY_LICH_NGUOI_XIN_VAO_DANG"}:
        if (
            looks_like_phieu_bo_sung(curr_header, curr_full)
            or looks_like_phieu_dang_vien(curr_header, curr_full)
            or curr_t.startswith("PHIEU_")
        ):
            return True, "ly_lich_to_phieu"
        if looks_like_kiem_diem_header(curr_header, curr_full) or curr_t.startswith(
            "BAN_TU_KIEM"
        ):
            return True, "ly_lich_to_kiem_diem"
        if looks_like_standalone_minutes(curr_header, curr_full):
            return True, "ly_lich_to_minutes"
        if looks_like_quyet_dinh_or_nghi_quyet(curr_header, curr_full):
            return True, "ly_lich_to_quyet_dinh"
        max_ll = soft_max_pages_for(open_t) or 18
        if open_page_count >= max_ll and (
            looks_like_ly_lich_header(curr_header, curr_full) or curr_t == open_t
        ):
            return True, "ly_lich_max_pages"
        if open_page_count >= max_ll and looks_like_ly_lich_header(
            curr_header, curr_full
        ):
            return True, "ly_lich_max_pages_header"

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
        if looks_like_kiem_diem_header(curr_header, curr_full) or curr_t.startswith(
            "BAN_TU_KIEM"
        ):
            return True, "phieu_dv_to_kiem_diem"
        max_pd = soft_max_pages_for(open_t) or 6
        if open_page_count >= max_pd and (
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
            # Sau ≥4 trang mà lại thấy tiêu đề Mẫu 3 / Phiếu bổ sung → form mới
            if open_page_count >= 4 and looks_like_phieu_bo_sung(
                curr_header, curr_full
            ):
                return True, "phieu_bo_sung_new_form_header"
        if looks_like_kiem_diem_header(curr_header, curr_full):
            return True, "phieu_bo_sung_to_kiem_diem"
        if looks_like_quyet_dinh_or_nghi_quyet(curr_header, curr_full):
            return True, "phieu_bo_sung_to_quyet_dinh"
        if looks_like_standalone_minutes(curr_header, curr_full):
            return True, "phieu_bo_sung_to_minutes"

    # Bản tự kiểm điểm: năm khác hoặc chuyển sang phiếu bổ sung / form kiểm điểm mới
    if open_t.startswith("BAN_TU_KIEM"):
        if curr_t == open_t or looks_like_kiem_diem_header(curr_header, curr_full):
            if (
                open_year is not None
                and blob_year is not None
                and open_year != blob_year
            ):
                return True, f"kiem_diem_new_year({blob_year})"
            if open_page_count >= 5 and looks_like_kiem_diem_header(
                curr_header, curr_full
            ):
                return True, "kiem_diem_new_form_header"
        if looks_like_phieu_bo_sung(curr_header, curr_full) or looks_like_ke_khai_tai_san(
            curr_header, curr_full
        ):
            return True, "kiem_diem_to_phieu_bo_sung"
        if looks_like_phieu_dang_vien(curr_header, curr_full):
            return True, "kiem_diem_to_phieu_dang_vien"
        if looks_like_phieu_xin_y_kien(curr_header, curr_full):
            return True, "kiem_diem_to_phieu_xin_y_kien"
        if looks_like_standalone_minutes(curr_header, curr_full):
            return True, "kiem_diem_to_minutes"

    # Phiếu xin ý kiến / tổng hợp ý kiến: thường 1 trang — tách khi gặp kiểm điểm/phiếu/QĐ
    if open_t.startswith("TONG_HOP_Y_KIEN") or open_t.endswith("Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI"):
        if looks_like_kiem_diem_header(curr_header, curr_full) or curr_t.startswith(
            "BAN_TU_KIEM"
        ):
            return True, "y_kien_to_kiem_diem"
        if looks_like_phieu_bo_sung(curr_header, curr_full) or looks_like_ke_khai_tai_san(
            curr_header, curr_full
        ):
            return True, "y_kien_to_phieu_bo_sung"
        if looks_like_phieu_xin_y_kien(curr_header, curr_full) and open_page_count >= 1:
            return True, "y_kien_new_form"
        if looks_like_quyet_dinh_or_nghi_quyet(curr_header, curr_full):
            return True, "y_kien_to_quyet_dinh"
        if open_page_count >= 1 and curr_t and curr_t != open_t:
            return True, "y_kien_type_change"
        if open_page_count >= 1:
            # Mặc định 1 phiếu xin ý kiến / 1 file
            return True, "y_kien_single_page"

    # Quyết định: số QĐ khác hoặc đã có trang + match QĐ mới
    if is_quyet_dinh_type(open_t) and is_quyet_dinh_type(curr_t):
        if open_ref and curr_ref and open_ref != curr_ref:
            return True, f"quyet_dinh_ref_change({open_ref}->{curr_ref})"
        if open_page_count >= 1 and curr_t:
            # Mặc định 1 QĐ / 1 file khi trang sau cũng là QĐ catalog
            return True, "quyet_dinh_one_per_file"
    if is_quyet_dinh_type(open_t) or (open_t or "").upper().startswith("CAC_QUYET"):
        if looks_like_phieu_xin_y_kien(curr_header, curr_full):
            return True, "quyet_dinh_to_phieu_xin_y_kien"
        if looks_like_kiem_diem_header(curr_header, curr_full):
            return True, "quyet_dinh_to_kiem_diem"
        if looks_like_phieu_bo_sung(curr_header, curr_full):
            return True, "quyet_dinh_to_phieu_bo_sung"

    return False, ""
