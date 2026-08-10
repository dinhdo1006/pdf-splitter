"""
pipeline/party_doc_matcher.py
=============================
Khớp header OCR với PARTY_DOC_CATALOG (104 loại) — Phụ lục 1.

Chặn false-positive:
  • Trang MỤC LỤC (liệt kê tên tài liệu) → không NEW catalog
  • Mục số trong form lý lịch (22), 23) ĐÀO TẠO…) → không NEW
  • Alias quá ngắn (van bang, chung chi) đã bỏ
  • OCR méo "MC LC" / "TAI LIU" vẫn nhận là mục lục
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger
from rapidfuzz import fuzz
from unidecode import unidecode

import config
from pipeline.party_catalog import PARTY_DOC_CATALOG


# Header chung — không dùng làm tín hiệu NEW
_NOISE_PHRASES = (
    "dang cong san viet nam",
    "cong hoa xa hoi chu nghia viet nam",
    "doc lap tu do hanh phuc",
    "tinh uy",
    "thanh uy",
    "dang uy",
    "chi bo",
)

# Trang mục lục / bìa mục lục — không map sang 104 loại
_TOC_MARKERS = (
    "muc luc tai lieu trong ho so dang vien",
    "muc luc tai lieu trong ho so",
    "tai lieu trong ho so dang vien",
    "ly do khong co tai lieu",
    "ly do khong co",
    "co hoac khong",
    "ten tai lieu",
)

# Mục đánh số trong form lý lịch (không phải tài liệu mới)
_FORM_SECTION_RE = re.compile(
    r"(?:^|\s)(\d{1,2})\)\s*[A-Z]",
)
_FORM_SECTION_HINTS = (
    "tom tat qua trinh hoat dong",
    "dao tao boi duong",
    "chuyen mon nghiep vu",
    "dac diem lich su ban than",
    "quan he voi nuoc ngoai",
    "hoan canh gia dinh",
    "cam doan ki ten",
    "cam doan ky ten",
    "chung nhan cua cap uy",
    "nhung diem can chu y",
)

# Alias OCR / tiêu đề thực tế → doc_type_key (ưu tiên cao, dài hơn match trước)
# KHÔNG dùng alias ngắn dễ dính mục lục / mục form trừ khi size-gated
_ALIASES: list[tuple[str, str]] = [
    ("so yeu ly lich dang vien", "LY_LICH_DANG_VIEN"),
    ("so yeu ly lich", "LY_LICH_DANG_VIEN"),
    ("so luoc ly lich", "LY_LICH_DANG_VIEN"),
    ("ly lich cua nguoi xin vao dang", "LY_LICH_NGUOI_XIN_VAO_DANG"),
    ("ly lich nguoi xin vao dang", "LY_LICH_NGUOI_XIN_VAO_DANG"),
    ("ly lich xin vao dang", "LY_LICH_NGUOI_XIN_VAO_DANG"),
    ("ly lich dang vien", "LY_LICH_DANG_VIEN"),
    ("ho so ly lich", "LY_LICH_DANG_VIEN"),
    # "so ly lich" dễ dính field "Số lý lịch:" trên Phiếu ĐV → chuyển size-gated
    ("phieu bo sung ho so dang vien", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung thong tin dang vien", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung ho so", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3-hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3 hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mu 3-hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mu 3 hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3 - hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("bo sung ho so", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu dang vien cu", "PHIEU_DANG_VIEN_CU_LUU_LICH_SU"),
    ("phieu dang vien mau 2", "PHIEU_DANG_VIEN"),
    ("mau 2-hsdv", "PHIEU_DANG_VIEN"),
    ("mau 2 hsdv", "PHIEU_DANG_VIEN"),
    ("mu 2-hsdv", "PHIEU_DANG_VIEN"),  # OCR méo MẪU→MU
    ("mu 2 hsdv", "PHIEU_DANG_VIEN"),
    ("mau 2 - hsdv", "PHIEU_DANG_VIEN"),
    ("phieu dang vien", "PHIEU_DANG_VIEN"),
    ("ban tu kiem diem hang nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu kiem diem hang nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("kiem diem cuoi nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu kiem diem nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu danh gia", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu danh gia kiem diem", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu kiem diem", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    # Nghị quyết / giới thiệu
    ("nghi quyet gioi thieu doan vien", "NGHI_QUYET_GIOI_THIEU_DOAN_VIEN_UU_TU"),
    ("nghi quyet de nghi ket nap", "NGHI_QUYET_DE_NGHI_KET_NAP_CUA_CHI_BO"),
    ("nghi quyet cong nhan chinh thuc", "NGHI_QUYET_CONG_NHAN_CHINH_THUC_CHI_BO"),
    # Nghị quyết / chuẩn y công nhận ≈ QĐ công nhận (STT 07) trên hồ sơ thực tế
    ("nghi quyet cong nhan dang vien", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("chuan y cong nhan dang vien", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("chuan y cong nhan chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("ve viec chuan y cong nhan", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("ve viec cong nhan dang vien chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("- qd/tu", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    (" qd/tu", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("-qd/tu", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("qd/tu", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("qd-tu", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    # Văn bằng / chứng chỉ
    ("chung chi ly luan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("bang ly luan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("trung cap ly luan", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("cao cap ly luan", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("van bang chuyen mon", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi chuyen mon", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi ngoai ngu", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi tin hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang tot nghiep dai hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang tot nghiep", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    # KHÔNG dùng alias ngắn "kiem diem dang vien" / "ban kiem diem dang vien"
    # — dễ dính OCR "BAN KIEM DIEM" từ biên bản / form khác
    ("giay gioi thieu sinh hoat dang tam thoi", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI"),
    ("sinh hoat dang tam thoi", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI"),
    ("giay gioi thieu tam thoi", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI"),
    ("giay gioi thieu sinh hoat dang chinh thuc", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC"),
    ("giay gioi thieu sinh hoat dang noi bo", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NOI_BO"),
    ("sinh hoat dang noi bo", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NOI_BO"),
    ("gtshd noi bo", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NOI_BO"),
    ("giay gioi thieu sinh hoat dang", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC"),
    ("sinh hoat dang bo", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC"),
    ("quyet dinh ket nap lai", "QUYET_DINH_KET_NAP_LAI"),
    ("quyet dinh ket nap dang vien", "QUYET_DINH_KET_NAP_DANG_VIEN"),
    ("ket nap vao dang", "QUYET_DINH_KET_NAP_DANG_VIEN"),
    ("quyet dinh ket nap", "QUYET_DINH_KET_NAP_DANG_VIEN"),
    ("quyet dinh cong nhan dang vien chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("cong nhan dang vien chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("cong nhan chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("quyet dinh cong nhan dang vien", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("don xin vao dang", "DON_XIN_VAO_DANG"),
    ("quyet dinh ky luat", "QUYET_DINH_KY_LUAT_DANG"),
    ("hinh thuc ky luat", "QUYET_DINH_KY_LUAT_DANG"),
    ("quyet dinh khen thuong", "QUYET_DINH_KHEN_THUONG"),
    ("quyet dinh tang thuong", "QUYET_DINH_KHEN_THUONG"),
    ("chung nhan khen thuong", "QUYET_DINH_KHEN_THUONG"),
    ("giay gioi thieu nguoi vao dang", "GIAY_GIOI_THIEU_NGUOI_VAO_DANG"),
    ("gioi thieu doan vien uu tu", "GIAY_GIOI_THIEU_NGUOI_VAO_DANG"),
    ("cu nhan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("chung chi llct", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("bang dai hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang trung cap", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang cao dang", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi hanh nghe", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("giay chung nhan tot nghiep", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung nhan tot nghiep", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("quyet dinh dieu dong", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh bo nhiem", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh chuyen cong tac", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh nang luong", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh nang bac luong", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh tuyen dung", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
    ("quyet dinh tiep nhan", "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"),
]

# Alias ngắn — CHỈ khi page_size_group phù hợp (tránh false NEW trên A4)
_SIZE_GATED_ALIASES: list[tuple[str, str, frozenset[str]]] = [
    ("ly lich", "LY_LICH_DANG_VIEN", frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"})),
    ("so ly lich", "LY_LICH_DANG_VIEN", frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"})),
]

# Phụ lục không thuộc 104 — soft-attach, không tạo catalog key
_APPENDIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"mau\s*2a.*kd.*dg", re.I), "PHU_LUC_TU_KIEM_DIEM"),
    (re.compile(r"danh\s*gia.*phan\s*loai", re.I), "PHU_LUC_TU_KIEM_DIEM"),
    (re.compile(r"trich\s*bien\s*ban", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"bien\s*ban.*chi\s*bo", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"bien\s*ban.*chi\s*doan", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"hop\s*chi\s*bo.*xet\s*chuyen", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"bien\s*ban\s*hop", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"bien\s*ban\s*so", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"^bien\s*ban\b", re.I), "PHU_LUC_NGHI_QUYET"),
    (re.compile(r"nghi\s*quyet.*chi\s*bo", re.I), "PHU_LUC_NGHI_QUYET"),
]


@dataclass(frozen=True)
class MatchResult:
    doc_type_key: str
    score: float  # 0–100
    matched_phrase: str
    source: str  # "alias" | "catalog" | "none" | "toc" | "form_section" | "appendix"


def _collapse(text: str) -> str:
    """ASCII upper + collapse whitespace."""
    return " ".join(unidecode(text or "").upper().split())


def _is_noise_only(header_ascii: str) -> bool:
    h = header_ascii.lower()
    substantive = h
    for n in _NOISE_PHRASES:
        substantive = substantive.replace(n, " ")
    substantive = " ".join(substantive.split())
    return len(substantive) < 8


def _ocr_normalize_toc_blob(text: str) -> str:
    """Sửa méo OCR thường gặp trên trang mục lục (MC LC, TAI LIU…)."""
    b = _collapse(text or "").lower()
    # MỤC LỤC → MC LC / MUC LC / M C L C
    b = re.sub(r"\bm\s*c\s*l\s*c\b", "muc luc", b)
    b = re.sub(r"\bmuc\s*l\s*c\b", "muc luc", b)
    b = re.sub(r"\bmc\s*luc\b", "muc luc", b)
    b = re.sub(r"\bm\s*uc\s*luc\b", "muc luc", b)
    # TÀI LIỆU → TAI LIU
    b = re.sub(r"\btai\s*liu\b", "tai lieu", b)
    # ĐẢNG / ĐNG
    b = re.sub(r"\bdng\b", "dang", b)
    b = re.sub(r"\bho\s*so\s*dng\b", "ho so dang", b)
    return b


def is_table_of_contents(header_text: str, full_text: str = "") -> bool:
    """True nếu trang là mục lục hồ sơ (không phải thành phần tài liệu 104)."""
    blob = _ocr_normalize_toc_blob(
        (header_text or "") + "\n" + (full_text or "")[:1200]
    )
    if not blob:
        return False

    # Cột đặc trưng form mục lục HSĐV (kể cả khi OCR méo tiêu đề)
    if "ly do khong co" in blob:
        return True
    if "ten tai lieu" in blob and ("co" in blob or "khong" in blob):
        return True

    # Tiêu đề mục lục (sau normalize OCR)
    if "muc luc" in blob and (
        "tai lieu" in blob
        or "ho so dang" in blob
        or "ho so" in blob
        or "dang vien" in blob
        or "co khong" in blob
    ):
        return True

    hits = sum(1 for m in _TOC_MARKERS if m in blob)
    if hits >= 2:
        return True

    # Bìa "MỤC LỤC" lớn
    if blob.strip().startswith("muc luc") or " muc luc " in f" {blob} ":
        if any(x in blob for x in ("ho ten", "dang vien", "tai lieu", "ho so")):
            return True
    return False


def looks_like_toc_listing(
    full_text: str, alias_phrases: list[tuple[str, str]]
) -> bool:
    """Nhiều tên tài liệu catalog cùng trang → gần chắc là mục lục liệt kê."""
    blob = _ocr_normalize_toc_blob(full_text or "")
    if len(blob) < 40:
        return False
    keys: set[str] = set()
    for phrase, key in alias_phrases:
        p = phrase.lower()
        if len(p) < 14:
            continue
        if p in blob:
            keys.add(key)
            if len(keys) >= 3:
                return True
    return False


def is_ly_lich_form_section(header_text: str, full_text: str = "") -> bool:
    """
    True nếu đây là mục đánh số bên trong form lý lịch
    (vd. 22) Tóm tắt quá trình…, 23) Đào tạo bồi dưỡng…).
    Không áp dụng cho Phiếu ĐV / Phiếu bổ sung (cũng có mục 01), 02)…).
    """
    blob_raw = (header_text or "") + "\n" + (full_text or "")[:500]
    ascii_low = unidecode(blob_raw).lower()
    # Phiếu / Mẫu HSDV — không phải mục lý lịch
    if any(
        x in ascii_low
        for x in (
            "phieu dang vien",
            "phieu bo sung",
            "mau 2",
            "mu 2",
            "mau 3",
            "mu 3",
            "mau 2-hsdv",
            "mau 3-hsdv",
            "hsdv",
        )
    ):
        return False

    blob = _collapse(header_text + "\n" + (full_text or "")[:500])
    if not blob:
        return False
    head = blob[:120]
    if _FORM_SECTION_RE.search(head):
        return True
    low = blob.lower()
    if any(h in low for h in _FORM_SECTION_HINTS):
        if re.search(r"\b\d{1,2}\)", head) or re.search(r"\b\d{1,2}\)", blob[:80]):
            return True
        if any(
            h in low
            for h in (
                "cam doan",
                "hoan canh gia dinh",
                "chung nhan cua cap uy",
                "ban to chuc trung uong",
            )
        ):
            return True
    return False


class PartyDocMatcher:
    """Matcher header OCR → doc_type_key trong PARTY_DOC_CATALOG."""

    def __init__(
        self,
        fuzzy_threshold: float = getattr(
            config, "CATALOG_FUZZY_THRESHOLD", 82
        ),
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self._catalog_phrases: list[tuple[str, str]] = []
        for key, (_stt, ten, _prio) in PARTY_DOC_CATALOG.items():
            phrase = _collapse(ten)
            if phrase and len(phrase) >= 12:
                self._catalog_phrases.append((phrase, key))
        self._catalog_phrases.sort(key=lambda x: len(x[0]), reverse=True)

        self._aliases: list[tuple[str, str]] = [
            (_collapse(a), k) for a, k in _ALIASES
        ]
        self._aliases.sort(key=lambda x: len(x[0]), reverse=True)
        self._size_gated: list[tuple[str, str, frozenset[str]]] = [
            (_collapse(a), k, groups) for a, k, groups in _SIZE_GATED_ALIASES
        ]

        logger.debug(
            f"[party_doc_matcher] {len(self._aliases)} aliases, "
            f"{len(self._catalog_phrases)} catalog phrases, "
            f"{len(self._size_gated)} size-gated"
        )

    def match(
        self,
        header_text: str,
        full_text: str = "",
        page_size_group: str = "OTHER",
    ) -> MatchResult:
        header = _collapse(header_text)
        if not header:
            return MatchResult("", 0.0, "", "none")

        # Size-gated short aliases TRƯỚC noise filter (vd. "LY LICH" len=7)
        # Không dùng khi header rõ là Phiếu / Mẫu HSDV
        header_low = header.lower()
        looks_like_phieu = any(
            x in header_low
            for x in ("phieu dang", "mau 2", "mu 2", "hsdv", "mau 3")
        )
        if not looks_like_phieu:
            for phrase, key, allowed_groups in self._size_gated:
                if page_size_group not in allowed_groups:
                    continue
                if phrase and phrase in header:
                    return MatchResult(key, 95.0, phrase, "alias")

        if _is_noise_only(header):
            return MatchResult("", 0.0, "", "none")

        # Chặn mục lục / mục form TRƯỚC khi fuzzy catalog
        if is_table_of_contents(header_text, full_text):
            logger.debug("[matcher] TOC detected — skip catalog match")
            return MatchResult("", 0.0, "MUC_LUC", "toc")

        if looks_like_toc_listing(full_text or header_text, self._aliases):
            logger.debug("[matcher] TOC listing (multi titles) — skip catalog")
            return MatchResult("", 0.0, "MUC_LUC", "toc")

        blob_low = unidecode(
            (header_text or "") + "\n" + (full_text or "")[:400]
        ).lower()

        # Phiếu ĐV / bổ sung TRƯỚC form_section (tránh 01) Họ tên → orphan)
        looks_like_phieu_blob = looks_like_phieu or any(
            x in blob_low
            for x in (
                "mau 2",
                "mu 2",
                "mau 2-hsdv",
                "mau 3",
                "mu 3",
                "phieu dang vien",
                "phieu bo sung",
            )
        )
        if looks_like_phieu_blob:
            for phrase, key in self._aliases:
                if key not in {
                    "PHIEU_DANG_VIEN",
                    "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                    "PHIEU_DANG_VIEN_CU_LUU_LICH_SU",
                }:
                    continue
                if phrase and (phrase in header or phrase in _collapse(full_text or "")[:200]):
                    return MatchResult(key, 100.0, phrase, "alias")

        if is_ly_lich_form_section(header_text, full_text):
            logger.debug("[matcher] form section detected — skip catalog match")
            return MatchResult("", 0.0, "FORM_SECTION", "form_section")

        # Appendix soft patterns (không phải catalog 104)
        # Biên bản / trích biên bản ≠ bản tự kiểm điểm / quyết định
        if "tu kiem diem" not in blob_low:
            if (
                "bien ban" in blob_low
                or "trich bien" in blob_low
                or ("hop chi bo" in blob_low and "xet" in blob_low)
            ):
                logger.debug("[matcher] bien ban → appendix (not kiem diem)")
                return MatchResult("", 0.0, "PHU_LUC_NGHI_QUYET", "appendix")

        for pat, kind in _APPENDIX_PATTERNS:
            if pat.search(blob_low):
                logger.debug(f"[matcher] appendix detected: {kind}")
                return MatchResult("", 0.0, kind, "appendix")

        # Ưu tiên Phiếu ĐV khi có Mẫu 2 / HSDV (tránh SO LY LICH field)
        if looks_like_phieu_blob:
            for phrase, key in self._aliases:
                if key not in {
                    "PHIEU_DANG_VIEN",
                    "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                    "PHIEU_DANG_VIEN_CU_LUU_LICH_SU",
                }:
                    continue
                if phrase and phrase in header:
                    return MatchResult(key, 100.0, phrase, "alias")

        # QĐ dạng "6294 - QD/TU" / "123/QD-TU" (alias ngắn bị filter len<10)
        if re.search(
            r"\b\d{1,6}\s*[-–/]?\s*QD\s*[-–/]?\s*T[UƯ]\b",
            header,
            re.IGNORECASE,
        ):
            return MatchResult(
                "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM",
                96.0,
                "QD/TU",
                "alias",
            )

        # 1) Alias trên HEADER
        for phrase, key in self._aliases:
            if not phrase or len(phrase) < 10:
                continue
            # Chặn kiểm điểm khi header là biên bản
            if key.startswith("BAN_TU_KIEM") and "bien ban" in blob_low:
                continue
            if phrase in header:
                return MatchResult(key, 100.0, phrase, "alias")
            score = fuzz.partial_ratio(phrase, header)
            if score >= max(self.fuzzy_threshold, 90):
                if key.startswith("BAN_TU_KIEM") and "bien ban" in blob_low:
                    continue
                return MatchResult(key, float(score), phrase, "alias")

        # 1b) Alias mạnh trên FULL TEXT khi header OCR rác / quá ngắn
        # (chỉ phrase dài ≥18 để tránh false positive)
        full_collapsed = _collapse((full_text or "")[:900])
        if full_collapsed and len(header) < 40:
            for phrase, key in self._aliases:
                if not phrase or len(phrase) < 18:
                    continue
                if key.startswith("BAN_TU_KIEM") and "bien ban" in blob_low:
                    continue
                if phrase in full_collapsed:
                    return MatchResult(key, 92.0, phrase, "alias")
                # QD/TU trong body
            if re.search(
                r"\b\d{1,6}\s*[-–/]?\s*QD\s*[-–/]?\s*T[UƯ]\b",
                full_collapsed,
                re.IGNORECASE,
            ):
                return MatchResult(
                    "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM",
                    94.0,
                    "QD/TU",
                    "alias",
                )

        # 2) Catalog — yêu cầu phrase dài + match mạnh trên HEADER (không full page)
        if len(header) < 18:
            return MatchResult("", 0.0, "", "none")

        best: MatchResult | None = None
        for phrase, key in self._catalog_phrases:
            if len(phrase) < 14:
                continue
            if len(phrase) > len(header) + 8 and phrase[: max(18, len(header))] not in header:
                if len(header) < 28:
                    continue
            if phrase in header:
                cand = MatchResult(key, 100.0, phrase, "catalog")
                if best is None or len(phrase) > len(best.matched_phrase):
                    best = cand
                continue
            score = float(fuzz.partial_ratio(phrase, header))
            if score >= max(self.fuzzy_threshold, 88) and len(header) >= max(
                22, int(len(phrase) * 0.45)
            ):
                cand = MatchResult(key, score, phrase, "catalog")
                if best is None or cand.score > best.score or (
                    cand.score == best.score
                    and len(phrase) > len(best.matched_phrase)
                ):
                    best = cand

        if best is not None:
            return best
        return MatchResult("", 0.0, "", "none")

    def has_catalog_hit(
        self,
        header_text: str,
        full_text: str = "",
        page_size_group: str = "OTHER",
    ) -> bool:
        r = self.match(header_text, full_text, page_size_group=page_size_group)
        return bool(r.doc_type_key)


_default_matcher: PartyDocMatcher | None = None


def get_matcher() -> PartyDocMatcher:
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = PartyDocMatcher()
    return _default_matcher


def refine_unknown_group_types(
    groups: list,
    all_signals: dict,
) -> int:
    """
    Post-classify nhóm CHUA_XAC_DINH bằng matcher + heuristic trên vài trang đầu.
    Returns số group được gán lại catalog.
    """
    from pipeline.doc_identity import (
        looks_like_kiem_diem_header,
        looks_like_ly_lich_header,
        looks_like_phieu_bo_sung,
        looks_like_phieu_dang_vien,
        looks_like_quyet_dinh_or_nghi_quyet,
    )

    matcher = get_matcher()
    updated = 0
    for g in groups:
        key = (getattr(g, "doc_type", "") or "").upper()
        if key and key in PARTY_DOC_CATALOG and key != "CHUA_XAC_DINH":
            continue
        pages = list(getattr(g, "page_numbers", []) or [])[:4]
        if not pages:
            continue
        headers: list[str] = []
        fulls: list[str] = []
        size = getattr(g, "page_size_group", "OTHER") or "OTHER"
        for pn in pages:
            sig = all_signals.get(pn)
            if sig is None:
                continue
            headers.append(getattr(sig, "header_text", "") or "")
            fulls.append((getattr(sig, "full_text", "") or "")[:900])
            size = getattr(sig, "page_size_group", size) or size
        if not headers:
            continue
        header_blob = "\n".join(headers)
        full_blob = "\n".join(fulls)
        result = matcher.match(
            header_blob,
            full_blob,
            page_size_group=size,
        )
        new_key = (result.doc_type_key or "").upper()
        if not (new_key and new_key in PARTY_DOC_CATALOG):
            if looks_like_phieu_bo_sung(header_blob, full_blob):
                new_key = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
            elif looks_like_phieu_dang_vien(header_blob, full_blob):
                new_key = "PHIEU_DANG_VIEN"
            elif looks_like_kiem_diem_header(header_blob, full_blob):
                new_key = "BAN_TU_KIEM_DIEM_HANG_NAM"
            elif looks_like_ly_lich_header(header_blob, full_blob):
                new_key = "LY_LICH_DANG_VIEN"
            elif looks_like_quyet_dinh_or_nghi_quyet(header_blob, full_blob):
                new_key = "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"
            else:
                continue
        g.doc_type = new_key
        if not getattr(g, "raw_title", None) and result.matched_phrase:
            g.raw_title = result.matched_phrase
        updated += 1
        logger.info(
            f"[refine] group #{getattr(g, 'group_id', '?')} "
            f"CHUA_XAC_DINH → {new_key} (score={result.score:.1f})"
        )
    return updated
