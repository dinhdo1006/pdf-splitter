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
    ("so ly lich", "LY_LICH_DANG_VIEN"),
    ("phieu bo sung ho so dang vien", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung thong tin dang vien", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung ho so", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3-hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3 hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("bo sung ho so", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu dang vien cu", "PHIEU_DANG_VIEN_CU_LUU_LICH_SU"),
    ("phieu dang vien mau 2", "PHIEU_DANG_VIEN"),
    ("mau 2-hsdv", "PHIEU_DANG_VIEN"),
    ("mau 2 hsdv", "PHIEU_DANG_VIEN"),
    ("phieu dang vien", "PHIEU_DANG_VIEN"),
    ("ban kiem diem dang vien", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu kiem diem hang nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu kiem diem hang nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("kiem diem cuoi nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu kiem diem nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu danh gia", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("tu danh gia kiem diem", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("kiem diem dang vien", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu kiem diem", "BAN_TU_KIEM_DIEM_HANG_NAM"),
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
    ("chung chi ly luan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("bang ly luan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("trung cap ly luan", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("cao cap ly luan", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("cu nhan chinh tri", "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI"),
    ("bang tot nghiep dai hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang tot nghiep", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi ngoai ngu", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi tin hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang dai hoc", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang trung cap", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("bang cao dang", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi hanh nghe", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
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
]

# Phụ lục không thuộc 104 — soft-attach, không tạo catalog key
_APPENDIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"mau\s*2a.*kd.*dg", re.I), "PHU_LUC_TU_KIEM_DIEM"),
    (re.compile(r"danh\s*gia.*phan\s*loai", re.I), "PHU_LUC_TU_KIEM_DIEM"),
    (re.compile(r"bien\s*ban.*chi\s*bo", re.I), "PHU_LUC_NGHI_QUYET"),
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
    """
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

        if is_ly_lich_form_section(header_text, full_text):
            logger.debug("[matcher] form section detected — skip catalog match")
            return MatchResult("", 0.0, "FORM_SECTION", "form_section")

        # Appendix soft patterns (không phải catalog 104)
        blob_low = unidecode(
            (header_text or "") + "\n" + (full_text or "")[:400]
        ).lower()
        for pat, kind in _APPENDIX_PATTERNS:
            if pat.search(blob_low):
                logger.debug(f"[matcher] appendix detected: {kind}")
                return MatchResult("", 0.0, kind, "appendix")

        # 1) Alias (chỉ substring đủ dài; fuzzy cao hơn)
        for phrase, key in self._aliases:
            if not phrase or len(phrase) < 10:
                continue
            if phrase in header:
                return MatchResult(key, 100.0, phrase, "alias")
            score = fuzz.partial_ratio(phrase, header)
            if score >= max(self.fuzzy_threshold, 90):
                return MatchResult(key, float(score), phrase, "alias")

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
