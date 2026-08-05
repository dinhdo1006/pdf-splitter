"""
pipeline/party_doc_matcher.py
=============================
Khớp header OCR với PARTY_DOC_CATALOG (104 loại) — Phụ lục 1.

Ưu tiên:
  1. Alias cố định (mã mẫu, biến thể tiêu đề thực tế)
  2. Tên tài liệu trong catalog (substring / fuzzy)
"""

from __future__ import annotations

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

# Alias OCR / tiêu đề thực tế → doc_type_key (ưu tiên cao, dài hơn match trước)
_ALIASES: list[tuple[str, str]] = [
    ("so yeu ly lich dang vien", "LY_LICH_DANG_VIEN"),
    ("so yeu ly lich", "LY_LICH_DANG_VIEN"),
    ("ly lich cua nguoi xin vao dang", "LY_LICH_NGUOI_XIN_VAO_DANG"),
    ("ly lich nguoi xin vao dang", "LY_LICH_NGUOI_XIN_VAO_DANG"),
    ("ly lich dang vien", "LY_LICH_DANG_VIEN"),
    ("phieu bo sung ho so dang vien", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu bo sung", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3-hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("mau 3 hsdv", "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"),
    ("phieu dang vien cu", "PHIEU_DANG_VIEN_CU_LUU_LICH_SU"),
    ("phieu dang vien", "PHIEU_DANG_VIEN"),
    ("ban kiem diem dang vien", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu kiem diem", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("ban tu kiem diem hang nam", "BAN_TU_KIEM_DIEM_HANG_NAM"),
    ("giay gioi thieu sinh hoat dang tam thoi", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI"),
    ("giay gioi thieu sinh hoat dang chinh thuc", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC"),
    ("giay gioi thieu sinh hoat dang noi bo", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NOI_BO"),
    ("giay gioi thieu sinh hoat dang", "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC"),
    ("quyet dinh ket nap lai", "QUYET_DINH_KET_NAP_LAI"),
    ("quyet dinh ket nap dang vien", "QUYET_DINH_KET_NAP_DANG_VIEN"),
    ("quyet dinh cong nhan dang vien chinh thuc", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("quyet dinh cong nhan dang vien", "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC"),
    ("don xin vao dang", "DON_XIN_VAO_DANG"),
    ("bang tot nghiep", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("chung chi", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
    ("van bang", "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON"),
]


@dataclass(frozen=True)
class MatchResult:
    doc_type_key: str
    score: float  # 0–100
    matched_phrase: str
    source: str  # "alias" | "catalog" | "none"


def _collapse(text: str) -> str:
    """ASCII upper + collapse whitespace."""
    return " ".join(unidecode(text or "").upper().split())


def _is_noise_only(header_ascii: str) -> bool:
    """True nếu header gần như chỉ chứa cụm hành chính chung."""
    h = header_ascii.lower()
    # Nếu có cụm catalog dài hơn noise thì không coi là noise-only
    substantive = h
    for n in _NOISE_PHRASES:
        substantive = substantive.replace(n, " ")
    substantive = " ".join(substantive.split())
    return len(substantive) < 8


class PartyDocMatcher:
    """Matcher header OCR → doc_type_key trong PARTY_DOC_CATALOG."""

    def __init__(
        self,
        fuzzy_threshold: float = getattr(
            config, "CATALOG_FUZZY_THRESHOLD", 82
        ),
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        # (phrase_ascii_upper, key) dài hơn trước
        self._catalog_phrases: list[tuple[str, str]] = []
        for key, (_stt, ten, _prio) in PARTY_DOC_CATALOG.items():
            phrase = _collapse(ten)
            if phrase:
                self._catalog_phrases.append((phrase, key))
        self._catalog_phrases.sort(key=lambda x: len(x[0]), reverse=True)

        self._aliases: list[tuple[str, str]] = [
            (_collapse(a), k) for a, k in _ALIASES
        ]
        self._aliases.sort(key=lambda x: len(x[0]), reverse=True)

        logger.debug(
            f"[party_doc_matcher] {len(self._aliases)} aliases, "
            f"{len(self._catalog_phrases)} catalog phrases"
        )

    def match(self, header_text: str) -> MatchResult:
        header = _collapse(header_text)
        if not header or _is_noise_only(header):
            return MatchResult("", 0.0, "", "none")

        # 1) Alias exact / substring
        for phrase, key in self._aliases:
            if phrase and phrase in header:
                return MatchResult(key, 100.0, phrase, "alias")
            score = fuzz.partial_ratio(phrase, header)
            if score >= max(self.fuzzy_threshold, 88):
                return MatchResult(key, float(score), phrase, "alias")

        # 2) Catalog names
        best: MatchResult | None = None
        for phrase, key in self._catalog_phrases:
            if len(phrase) < 10:
                # Tránh match quá ngắn gây nhiễu
                continue
            if phrase in header:
                cand = MatchResult(key, 100.0, phrase, "catalog")
                if best is None or cand.score > best.score or (
                    cand.score == best.score and len(phrase) > len(best.matched_phrase)
                ):
                    best = cand
                continue
            score = float(fuzz.partial_ratio(phrase, header))
            if score >= self.fuzzy_threshold:
                cand = MatchResult(key, score, phrase, "catalog")
                if best is None or cand.score > best.score or (
                    cand.score == best.score and len(phrase) > len(best.matched_phrase)
                ):
                    best = cand

        if best is not None:
            return best
        return MatchResult("", 0.0, "", "none")

    def has_catalog_hit(self, header_text: str) -> bool:
        return bool(self.match(header_text).doc_type_key)


# Singleton tiện dụng
_default_matcher: PartyDocMatcher | None = None


def get_matcher() -> PartyDocMatcher:
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = PartyDocMatcher()
    return _default_matcher
