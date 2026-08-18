"""
Module 5: Stateful boundary detection — 3-state anti-swallow model.

PageClass:
  NEW_DOCUMENT            — catalog hit hoặc score >= HIGH_THRESHOLD
  CONFIRMED_CONTINUATION  — ContinuationValidator / soft size / appendix xác nhận
  ORPHAN_PAGE             — không NEW và không xác nhận được → cách ly

Blank pages → SKIP (không orphan, không gộp).
CẤM: score thấp → tự động gộp vào tài liệu trước.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from loguru import logger
from rapidfuzz import fuzz
from unidecode import unidecode

import config
from pipeline.continuation_validator import (
    ContinuationValidator,
    MULTI_PAGE_FORM_TYPES,
    soft_max_pages_for,
)
from pipeline.doc_identity import (
    extract_decision_ref,
    is_quyet_dinh_type,
    looks_like_ke_khai_tai_san,
    looks_like_kiem_diem_header,
    looks_like_ly_lich_header,
    looks_like_phieu_bo_sung,
    looks_like_phieu_dang_vien,
    looks_like_quyet_dinh_or_nghi_quyet,
    looks_like_standalone_minutes,
    should_force_new_document,
)
from pipeline.signal_extractor import PageSignal
from pipeline.year_aware_sequencer import extract_year_robust

_STRONG_SIZE = getattr(
    config, "STRONG_SIZE_CONTINUATION_GROUPS", frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"})
)

# Doc types có thể nhận soft-appendix (Mẫu 2a, biên bản…)
_APPENDIX_HOST_TYPES = frozenset(
    {
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI",
        "BAN_TU_KIEM_DIEM_TAI_THOI_DIEM_CHUYEN",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_VI_PHAM",
        "LY_LICH_DANG_VIEN",
        "LY_LICH_NGUOI_XIN_VAO_DANG",
    }
)


class PageClass(Enum):
    NEW_DOCUMENT = "new_document"
    CONFIRMED_CONTINUATION = "confirmed_continuation"
    ORPHAN_PAGE = "orphan_page"
    # Giữ alias tương thích chỗ đọc cũ (nếu có)
    CONTINUATION = "confirmed_continuation"
    SEPARATOR = "skip_blank"


@dataclass
class BoundaryDecision:
    page_num: int
    page_class: PageClass
    score: float
    confidence: str
    reasoning: str


@dataclass
class DocumentGroup:
    group_id: int
    raw_title: str
    doc_type: str = "CHUA_XAC_DINH"
    doc_year: Optional[int] = None
    page_numbers: list[int] = field(default_factory=list)
    page_size_group: str = "OTHER"
    doc_ref: Optional[str] = None  # số QĐ / mã văn bản
    _sequence_number: int = field(default=0, repr=False, compare=False)
    _reattach_confidence: float = field(default=1.0, repr=False, compare=False)
    _is_tentative: bool = field(default=False, repr=False, compare=False)


def _header_similarity(a: str, b: str) -> float:
    if not a.strip() or not b.strip():
        return 0.0
    score_orig = fuzz.token_sort_ratio(a, b) / 100.0
    score_ascii = fuzz.token_sort_ratio(unidecode(a), unidecode(b)) / 100.0
    return max(score_orig, score_ascii)


def _clean_header(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:5]) if lines else text.strip()


class BoundaryDetector:
    """3-state page classifier — anti document-swallowing."""

    def __init__(
        self,
        boundary_threshold: float = config.BOUNDARY_THRESHOLD,
        low_confidence_range: tuple[float, float] = config.LOW_CONFIDENCE_RANGE,
        high_threshold: float = getattr(config, "HIGH_BOUNDARY_THRESHOLD", 0.70),
        continuation_validator: ContinuationValidator | None = None,
        llm_referee: Any = None,
    ) -> None:
        self.boundary_threshold = boundary_threshold
        self.low_confidence_range = low_confidence_range
        self.high_threshold = high_threshold
        self.validator = continuation_validator or ContinuationValidator()
        self.llm_referee = llm_referee

        self._prev_signal: PageSignal | None = None
        self._prev_was_blank: bool = False
        self._current_group: DocumentGroup | None = None
        self._groups: list[DocumentGroup] = []
        self._orphan_pages: list[int] = []
        self._blank_pages: list[int] = []
        self._low_confidence_pages: list[dict] = []
        self._group_counter: int = 0

    def _compute_score(self, signal: PageSignal) -> tuple[float, str]:
        score = 0.0
        factors: list[str] = []

        if signal.has_doc_keyword:
            score += config.SCORE_HAS_KEYWORD
            factors.append(
                f"+catalog({signal.matched_doc_type or signal.matched_keyword})"
            )

        if signal.has_large_centered_text:
            score += config.SCORE_HAS_LARGE_CENTERED
            factors.append("+large_centered")

        if self._prev_signal is not None:
            dens_delta = abs(signal.text_density - self._prev_signal.text_density)
            if dens_delta > 0.4:
                score += config.SCORE_DENSITY_CHANGE
                factors.append(f"+density_change({dens_delta:.2f})")

        if self._prev_was_blank:
            score += config.SCORE_AFTER_SEPARATOR
            factors.append("+after_blank")

        if signal.is_continuation:
            score += config.SCORE_IS_CONTINUATION
            factors.append("-continuation_pattern")

        if self._prev_signal is not None:
            sim = _header_similarity(signal.header_text, self._prev_signal.header_text)
            if sim > config.HEADER_SIMILARITY_THRESHOLD:
                score += config.SCORE_HEADER_SIMILAR
                factors.append(f"-header_similar({sim:.2f})")

        if signal.avg_confidence < 0.5:
            score += config.SCORE_LOW_OCR_CONFIDENCE
            factors.append("-low_ocr_conf")

        # === Page size group signals ===
        prev = self._prev_signal
        if (
            prev is not None
            and prev.page_size_group != signal.page_size_group
            and prev.page_size_group != "OTHER"
            and signal.page_size_group != "OTHER"
        ):
            score += getattr(config, "SCORE_SIZE_GROUP_CHANGE", 0.50)
            factors.append(
                f"+size_change({prev.page_size_group}->{signal.page_size_group})"
            )

        if (
            self._current_group is not None
            and self._current_group.page_size_group == signal.page_size_group
            and signal.page_size_group in _STRONG_SIZE
        ):
            bias = float(
                config.PAGE_SIZE_GROUPS.get(signal.page_size_group, {}).get(
                    "continuation_bias", 0.0
                )
            )
            if bias > 0:
                score -= bias
                factors.append(f"-size_cont_bias({bias})")

        # Prev page end-of-doc → boost NEW
        eod_min = getattr(config, "EOD_MIN_CONFIDENCE", 0.65)
        if (
            prev is not None
            and getattr(prev, "is_likely_end_of_doc", False)
            and getattr(prev, "end_of_doc_confidence", 0.0) >= eod_min
        ):
            score += getattr(config, "SCORE_PREV_END_OF_DOC", 0.35)
            factors.append(
                f"+prev_eod({prev.end_of_doc_confidence:.2f})"
            )

        score = float(min(1.0, max(0.0, score)))
        reasoning = "; ".join(factors) if factors else "no_signals"
        return score, reasoning

    def _confidence_label(self, score: float) -> str:
        lo, hi = self.low_confidence_range
        if score >= 0.65 or score <= 0.20:
            return "high"
        if lo <= score <= hi:
            return "low"
        return "medium"

    def _open_new_group(self, signal: PageSignal, score: float, reason: str) -> None:
        if self._current_group is not None:
            self._groups.append(self._current_group)
            logger.info(
                f"Closed document #{self._current_group.group_id} "
                f"({len(self._current_group.page_numbers)} pages)"
            )

        doc_type = signal.matched_doc_type or "CHUA_XAC_DINH"
        # Size hint khi chưa match catalog trên booklet/landscape
        if doc_type == "CHUA_XAC_DINH" and signal.page_size_group in _STRONG_SIZE:
            hint = config.PAGE_SIZE_GROUPS.get(signal.page_size_group, {}).get(
                "doc_type_hint"
            )
            if hint:
                doc_type = hint

        # Phiếu bổ sung ưu tiên hơn nếu header rõ
        if looks_like_phieu_bo_sung(signal.header_text, signal.full_text):
            doc_type = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"

        blob = (signal.header_text or "") + "\n" + (signal.full_text or "")[:500]
        doc_ref = extract_decision_ref(blob)
        doc_year = extract_year_robust(blob)

        self._group_counter += 1
        self._current_group = DocumentGroup(
            group_id=self._group_counter,
            raw_title=_clean_header(signal.header_text),
            doc_type=doc_type,
            doc_year=doc_year,
            page_numbers=[signal.page_num],
            page_size_group=signal.page_size_group or "OTHER",
            doc_ref=doc_ref,
        )
        logger.info(
            f"NEW_DOCUMENT #{self._group_counter} at page {signal.page_num} "
            f"(score={score:.2f}, doc_type={doc_type!r}, "
            f"size={signal.page_size_group}, ref={doc_ref!r}) | {reason}"
        )

    def _append_continuation(self, signal: PageSignal, reason: str) -> bool:
        if self._current_group is None:
            return False
        open_t = (self._current_group.doc_type or "").upper()
        max_soft = soft_max_pages_for(open_t)
        if max_soft is not None and len(self._current_group.page_numbers) >= max_soft:
            logger.info(
                f"Page {signal.page_num}: soft-cont blocked — "
                f"{open_t} already {len(self._current_group.page_numbers)} pages "
                f"(max={max_soft})"
            )
            self._close_current_group(f"max_pages_{open_t}({max_soft})")
            return False
        self._current_group.page_numbers.append(signal.page_num)
        logger.debug(
            f"Page {signal.page_num}: CONFIRMED_CONTINUATION → "
            f"group #{self._current_group.group_id} | {reason}"
        )
        return True

    def _page_looks_like_new_form(self, signal: PageSignal) -> bool:
        if signal.matched_doc_type:
            return True
        h = signal.header_text or ""
        f = signal.full_text or ""
        return (
            looks_like_phieu_bo_sung(h, f)
            or looks_like_phieu_dang_vien(h, f)
            or looks_like_kiem_diem_header(h, f)
            or looks_like_ly_lich_header(h, f)
            or looks_like_quyet_dinh_or_nghi_quyet(h, f)
        )

    def _continue_or_new_or_orphan(
        self,
        signal: PageSignal,
        score: float,
        score_reason: str,
        cont_reason: str,
    ) -> tuple[PageClass, str]:
        """Thử soft-continue; soft-max → NEW (cùng loại) thay vì orphan khi có thể."""
        if self._append_continuation(signal, cont_reason):
            return (
                PageClass.CONFIRMED_CONTINUATION,
                f"confirmed_cont[{cont_reason}] | {score_reason}",
            )
        # Soft-max vừa đóng group — ưu tiên mở NEW đúng loại (không copy loại cũ)
        if self._page_looks_like_new_form(signal):
            if not signal.matched_doc_type:
                h = signal.header_text or ""
                f = signal.full_text or ""
                if looks_like_kiem_diem_header(h, f):
                    signal.matched_doc_type = "BAN_TU_KIEM_DIEM_HANG_NAM"
                    signal.has_doc_keyword = True
                elif looks_like_phieu_bo_sung(h, f):
                    signal.matched_doc_type = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
                    signal.has_doc_keyword = True
                elif looks_like_phieu_dang_vien(h, f):
                    signal.matched_doc_type = "PHIEU_DANG_VIEN"
                    signal.has_doc_keyword = True
                elif looks_like_ly_lich_header(h, f):
                    signal.matched_doc_type = "LY_LICH_DANG_VIEN"
                    signal.has_doc_keyword = True
                elif looks_like_quyet_dinh_or_nghi_quyet(h, f):
                    signal.matched_doc_type = "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"
                    signal.has_doc_keyword = True
            self._open_new_group(
                signal, score, f"after_soft_max|{cont_reason}|{score_reason}"
            )
            return PageClass.NEW_DOCUMENT, f"new[after_soft_max] | {score_reason}"
        # Form đa trang còn tiếp (cùng khổ / mid-page) → tách file mới cùng loại
        # Không copy loại nếu trang đã là form khác (kiểm điểm / QĐ / biên bản)
        if self._groups:
            last = self._groups[-1]
            last_t = (last.doc_type or "").upper()
            if last_t in MULTI_PAGE_FORM_TYPES or last_t.startswith("BAN_TU_KIEM"):
                h = signal.header_text or ""
                f = signal.full_text or ""
                if looks_like_kiem_diem_header(h, f) and not last_t.startswith(
                    "BAN_TU_KIEM"
                ):
                    signal.matched_doc_type = "BAN_TU_KIEM_DIEM_HANG_NAM"
                    signal.has_doc_keyword = True
                    self._open_new_group(
                        signal,
                        score,
                        f"split_kiem_diem_after_{last_t}|{score_reason}",
                    )
                    return (
                        PageClass.NEW_DOCUMENT,
                        f"new[split_kiem_diem] | {score_reason}",
                    )
                # Sau kiểm điểm: phiếu / kê khai tài sản → NEW phiếu, không copy BAN
                if last_t.startswith("BAN_TU_KIEM") and (
                    looks_like_phieu_bo_sung(h, f)
                    or looks_like_ke_khai_tai_san(h, f)
                    or looks_like_phieu_dang_vien(h, f)
                ):
                    if looks_like_phieu_dang_vien(h, f):
                        signal.matched_doc_type = "PHIEU_DANG_VIEN"
                    else:
                        signal.matched_doc_type = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
                    signal.has_doc_keyword = True
                    self._open_new_group(
                        signal,
                        score,
                        f"split_phieu_after_kiem_diem|{score_reason}",
                    )
                    return (
                        PageClass.NEW_DOCUMENT,
                        f"new[split_phieu_after_kd] | {score_reason}",
                    )
                if looks_like_quyet_dinh_or_nghi_quyet(h, f):
                    self._mark_orphan(signal, f"quyet_dinh_after_soft_max|{last_t}")
                    return (
                        PageClass.ORPHAN_PAGE,
                        f"orphan[quyet_dinh_after_form] | {score_reason}",
                    )
                # Không copy loại BAN sang mid-page không có tín hiệu kiểm điểm
                if last_t.startswith("BAN_TU_KIEM") and not looks_like_kiem_diem_header(
                    h, f
                ):
                    self._mark_orphan(
                        signal, f"no_copy_ban_type_onto_mid|{score_reason}"
                    )
                    return (
                        PageClass.ORPHAN_PAGE,
                        f"orphan[no_copy_ban] | {score_reason}",
                    )
                same_size = (
                    signal.page_size_group == last.page_size_group
                    and signal.page_size_group != "OTHER"
                )
                mid_like = (
                    not getattr(signal, "is_toc", False)
                    and not looks_like_standalone_minutes(h, f)
                    and (signal.text_density or 0) >= 0.01
                )
                if same_size or mid_like:
                    if not signal.matched_doc_type:
                        signal.matched_doc_type = last.doc_type
                        signal.has_doc_keyword = True
                    self._open_new_group(
                        signal,
                        score,
                        f"split_after_soft_max({last_t})|{cont_reason}|{score_reason}",
                    )
                    return (
                        PageClass.NEW_DOCUMENT,
                        f"new[split_after_soft_max] | {score_reason}",
                    )
        self._mark_orphan(signal, f"soft_max_or_no_open | {cont_reason}")
        return PageClass.ORPHAN_PAGE, f"orphan[soft_max_or_no_open] | {score_reason}"

    def _mark_orphan(self, signal: PageSignal, reason: str) -> None:
        """
        Cách ly trang mồ côi.
        Quyết định: luôn đóng. Form đa trang: đóng khi orphan là TOC/biên bản/appendix
        (không đóng vì soft-cont fail giữa các trang form).
        """
        self._orphan_pages.append(signal.page_num)
        open_id = self._current_group.group_id if self._current_group else None
        logger.warning(
            f"Page {signal.page_num}: ORPHAN_PAGE (group #{open_id} vẫn mở) — {reason}"
        )
        if self._current_group is None:
            return
        open_t = (self._current_group.doc_type or "").upper()
        if is_quyet_dinh_type(open_t):
            self._close_current_group("orphan_closes_quyet_dinh")
            return
        if open_t.startswith("NGHI_QUYET"):
            self._close_current_group("orphan_closes_decision_like")
            return
        r = (reason or "").lower()
        boundary_orphan = any(
            x in r
            for x in (
                "toc",
                "minutes",
                "appendix",
                "bien_ban",
                "quyet_dinh",
                "nghi_quyet",
            )
        )
        if not boundary_orphan:
            return
        if open_t == "PHIEU_DANG_VIEN":
            self._close_current_group("orphan_closes_phieu_dang_vien")
            return
        if open_t in MULTI_PAGE_FORM_TYPES or open_t.startswith("BAN_TU_KIEM"):
            self._close_current_group(f"orphan_closes_{open_t.lower()}")

    def _close_current_group(self, reason: str) -> None:
        """Đóng group đang mở (giữ orphan list / không tạo orphan)."""
        if self._current_group is None:
            return
        logger.info(
            f"Closed document #{self._current_group.group_id} "
            f"({len(self._current_group.page_numbers)} pages) — {reason}"
        )
        self._groups.append(self._current_group)
        self._current_group = None

    def _soft_size_continuation(self, signal: PageSignal) -> bool:
        """
        Soft CONFIRMED_CONTINUATION khi cùng BOOKLET/LANDSCAPE với group đang mở
        và không có catalog hit loại khác.
        Không áp dụng cho A4 / OTHER.
        """
        if self._current_group is None:
            return False
        sg = signal.page_size_group
        if sg not in _STRONG_SIZE:
            return False
        if self._current_group.page_size_group != sg:
            return False
        # Catalog hit loại KHÁC → không soft cont
        if signal.matched_doc_type:
            open_type = (self._current_group.doc_type or "").upper()
            if signal.matched_doc_type.upper() != open_type:
                return False
        # TOC không bao giờ soft-size
        if getattr(signal, "is_toc", False):
            return False
        return True

    def _maybe_close_on_size_hard_boundary(self, signal: PageSignal) -> None:
        """
        Booklet/landscape → A4 là ranh giới cứng vật lý: đóng group đang mở
        để trang A4 không bị soft-absorb vào lý lịch booklet.
        """
        if self._current_group is None:
            return
        open_sg = self._current_group.page_size_group
        curr_sg = signal.page_size_group or "OTHER"
        a4 = frozenset({"A4_PORTRAIT", "A4_MEDIUM"})
        if open_sg in _STRONG_SIZE and curr_sg in a4:
            self._close_current_group(
                f"size_hard_boundary({open_sg}->{curr_sg})"
            )

    def process_page(self, signal: PageSignal) -> BoundaryDecision:
        # 1) Blank → SKIP (không orphan) — ghi nhận để audit đủ trang
        if signal.is_blank:
            self._blank_pages.append(signal.page_num)
            decision = BoundaryDecision(
                page_num=signal.page_num,
                page_class=PageClass.SEPARATOR,
                score=0.0,
                confidence="high",
                reasoning="skip_blank",
            )
            self._prev_signal = signal
            self._prev_was_blank = True
            logger.debug(f"Page {signal.page_num}: SKIP_BLANK")
            return decision

        # Ranh giới cứng booklet/landscape → A4
        self._maybe_close_on_size_hard_boundary(signal)

        score, score_reason = self._compute_score(signal)
        confidence = self._confidence_label(score)

        open_doc = (
            (self._current_group.doc_type or "").upper()
            if self._current_group
            else ""
        )
        is_ly_lich_open = open_doc in {
            "LY_LICH_DANG_VIEN",
            "LY_LICH_NGUOI_XIN_VAO_DANG",
        }

        # Soft appendix (Mẫu 2a…) → gộp vào host kiểm điểm nếu đang mở
        # KHÔNG soft-attach biên bản thuần vào kiểm điểm (xử lý ở matcher)
        if getattr(signal, "is_appendix", False) and self._current_group is not None:
            kind = getattr(signal, "appendix_kind", "") or ""
            if kind == "PHU_LUC_NGHI_QUYET" and not open_doc.startswith("BAN_TU_KIEM"):
                # Biên bản / nghị quyết độc lập → orphan; đóng QĐ nếu đang mở
                if is_quyet_dinh_type(open_doc):
                    self._close_current_group("appendix_minutes_closes_quyet_dinh")
                self._mark_orphan(signal, f"appendix_standalone[{kind}]")
                decision = BoundaryDecision(
                    page_num=signal.page_num,
                    page_class=PageClass.ORPHAN_PAGE,
                    score=score,
                    confidence=confidence,
                    reasoning=f"orphan[appendix_standalone] | {score_reason}",
                )
                self._prev_signal = signal
                self._prev_was_blank = False
                return decision
            if open_doc in _APPENDIX_HOST_TYPES or open_doc.startswith("BAN_TU_KIEM"):
                if self._append_continuation(
                    signal, f"appendix_soft[{kind}]"
                ):
                    decision = BoundaryDecision(
                        page_num=signal.page_num,
                        page_class=PageClass.CONFIRMED_CONTINUATION,
                        score=score,
                        confidence=confidence,
                        reasoning=f"confirmed_cont[appendix] | {score_reason}",
                    )
                    self._prev_signal = signal
                    self._prev_was_blank = False
                    return decision

        # Đang mở form đa trang / QĐ + trang hiện tại giống biên bản → orphan
        if (
            self._current_group is not None
            and (
                is_quyet_dinh_type(open_doc)
                or open_doc in MULTI_PAGE_FORM_TYPES
                or open_doc.startswith("BAN_TU_KIEM")
            )
            and looks_like_standalone_minutes(signal.header_text, signal.full_text or "")
            and not (signal.matched_doc_type or "").upper().startswith("QUYET_DINH")
        ):
            close_why = f"minutes_after_{open_doc.lower() or 'open'}"
            self._close_current_group(close_why)
            self._mark_orphan(signal, close_why)
            decision = BoundaryDecision(
                page_num=signal.page_num,
                page_class=PageClass.ORPHAN_PAGE,
                score=score,
                confidence=confidence,
                reasoning=f"orphan[minutes_after_open] | {score_reason}",
            )
            self._prev_signal = signal
            self._prev_was_blank = False
            return decision

        # Đang mở phiếu ĐV + trang giống QĐ/nghị quyết → NEW catalog nếu match, else orphan
        if (
            self._current_group is not None
            and open_doc == "PHIEU_DANG_VIEN"
            and looks_like_quyet_dinh_or_nghi_quyet(
                signal.header_text, signal.full_text or ""
            )
            and not looks_like_phieu_dang_vien(
                signal.header_text, signal.full_text or ""
            )
        ):
            self._close_current_group("quyet_dinh_after_phieu_dang_vien")
            # Để nhánh is_new phía dưới mở group QĐ nếu matcher đã gắn type
            if not signal.matched_doc_type:
                score_reason += "; +force_after_phieu_close"

        # TOC: LUÔN orphan — đóng mọi form đa trang / booklet đang mở
        if getattr(signal, "is_toc", False):
            if self._current_group is not None:
                open_t = (self._current_group.doc_type or "").upper()
                if (
                    self._current_group.page_size_group in _STRONG_SIZE
                    or open_t in MULTI_PAGE_FORM_TYPES
                    or open_t.startswith("BAN_TU_KIEM")
                    or is_quyet_dinh_type(open_t)
                ):
                    self._close_current_group("toc_closes_open_document")
            self._mark_orphan(signal, "toc_not_catalog_document")
            decision = BoundaryDecision(
                page_num=signal.page_num,
                page_class=PageClass.ORPHAN_PAGE,
                score=score,
                confidence=confidence,
                reasoning=f"orphan[toc] | {score_reason}",
            )
            self._prev_signal = signal
            self._prev_was_blank = False
            return decision

        # Mục form lý lịch (22), 23)…): gộp khi đang mở LL / phiếu / kiểm điểm
        if getattr(signal, "is_form_section", False):
            open_multi = open_doc in {
                "LY_LICH_DANG_VIEN",
                "LY_LICH_NGUOI_XIN_VAO_DANG",
                "PHIEU_DANG_VIEN",
                "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
            } or open_doc.startswith("BAN_TU_KIEM")
            if open_multi:
                cont_ok = False
                if is_ly_lich_open:
                    cont_ok = self._soft_size_continuation(
                        signal
                    ) and self._append_continuation(
                        signal, "form_section_inside_ly_lich"
                    )
                else:
                    cont_ok = self._append_continuation(
                        signal, "form_section_inside_open_form"
                    )
                if cont_ok:
                    decision = BoundaryDecision(
                        page_num=signal.page_num,
                        page_class=PageClass.CONFIRMED_CONTINUATION,
                        score=score,
                        confidence=confidence,
                        reasoning=(
                            f"confirmed_cont[form_section_open_form] | {score_reason}"
                        ),
                    )
                    self._prev_signal = signal
                    self._prev_was_blank = False
                    return decision
                # Soft-max vừa đóng — thử NEW nếu header form
                if self._page_looks_like_new_form(signal):
                    self._open_new_group(
                        signal, score, f"form_section_after_soft_max|{score_reason}"
                    )
                    decision = BoundaryDecision(
                        page_num=signal.page_num,
                        page_class=PageClass.NEW_DOCUMENT,
                        score=score,
                        confidence=confidence,
                        reasoning=f"new[form_section_after_soft_max] | {score_reason}",
                    )
                    self._prev_signal = signal
                    self._prev_was_blank = False
                    return decision
            self._mark_orphan(signal, "form_section_not_catalog_document")
            decision = BoundaryDecision(
                page_num=signal.page_num,
                page_class=PageClass.ORPHAN_PAGE,
                score=score,
                confidence=confidence,
                reasoning=f"orphan[form_section] | {score_reason}",
            )
            self._prev_signal = signal
            self._prev_was_blank = False
            return decision

        # 2) NEW: catalog hit hoặc high heuristic score
        is_new = bool(signal.matched_doc_type) or score >= self.high_threshold

        # Override: phiếu bổ sung khi đang mở / vừa match phiếu ĐV
        effective_type = signal.matched_doc_type or ""
        if looks_like_phieu_bo_sung(signal.header_text, signal.full_text):
            effective_type = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
            signal.matched_doc_type = effective_type
            signal.has_doc_keyword = True
            is_new = True
            score_reason += "; +force_phieu_bo_sung"

        # Force NEW: phiếu ↔ kiểm điểm / QĐ… (kể cả khi chưa match catalog)
        if self._current_group is not None:
            force, force_reason = should_force_new_document(
                self._current_group.doc_type,
                self._current_group.doc_year,
                self._current_group.doc_ref,
                len(self._current_group.page_numbers),
                effective_type or signal.matched_doc_type,
                signal.header_text,
                signal.full_text or "",
            )
            if force:
                is_new = True
                score_reason += f"; +force_new[{force_reason}]"
                # Gán loại từ heuristic nếu catalog miss
                if not (effective_type or signal.matched_doc_type):
                    if looks_like_kiem_diem_header(
                        signal.header_text, signal.full_text or ""
                    ):
                        signal.matched_doc_type = "BAN_TU_KIEM_DIEM_HANG_NAM"
                        signal.has_doc_keyword = True
                    elif looks_like_phieu_bo_sung(
                        signal.header_text, signal.full_text or ""
                    ) or looks_like_ke_khai_tai_san(
                        signal.header_text, signal.full_text or ""
                    ):
                        signal.matched_doc_type = "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
                        signal.has_doc_keyword = True
                    elif looks_like_phieu_dang_vien(
                        signal.header_text, signal.full_text or ""
                    ):
                        signal.matched_doc_type = "PHIEU_DANG_VIEN"
                        signal.has_doc_keyword = True
                    else:
                        from pipeline.doc_identity import looks_like_phieu_xin_y_kien

                        if looks_like_phieu_xin_y_kien(
                            signal.header_text, signal.full_text or ""
                        ):
                            signal.matched_doc_type = (
                                "TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI"
                            )
                            signal.has_doc_keyword = True
                        elif looks_like_quyet_dinh_or_nghi_quyet(
                            signal.header_text, signal.full_text or ""
                        ):
                            signal.matched_doc_type = (
                                "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"
                            )
                            signal.has_doc_keyword = True

        # Form nhiều trang: tiêu đề catalog lặp lại trên trang tiếp → không NEW
        # Ngoại lệ: đổi page_size_group (vd. landscape sơ yếu → booklet lý lịch)
        if (
            is_new
            and self._current_group is not None
            and signal.matched_doc_type
            and signal.matched_doc_type == self._current_group.doc_type
            and signal.matched_doc_type in MULTI_PAGE_FORM_TYPES
            and "+force_new" not in score_reason
        ):
            size_changed = (
                signal.page_size_group != "OTHER"
                and self._current_group.page_size_group != "OTHER"
                and signal.page_size_group != self._current_group.page_size_group
            )
            if size_changed:
                is_new = True
                score_reason += (
                    f"; +same_type_size_change("
                    f"{self._current_group.page_size_group}->{signal.page_size_group})"
                )
            else:
                curr_year = extract_year_robust(
                    signal.header_text + "\n" + (signal.full_text or "")[:300]
                )
                group_year = self._current_group.doc_year
                if (
                    curr_year is not None
                    and group_year is not None
                    and curr_year != group_year
                ):
                    is_new = True
                    score_reason += f"; +same_type_new_year({curr_year})"
                else:
                    is_new = False
                    score_reason += "; -repeated_catalog_header_same_type"
                    if curr_year is not None and self._current_group.doc_year is None:
                        self._current_group.doc_year = curr_year

        # Soft size continuation: cùng booklet/landscape → không NEW nếu chỉ heuristic
        if (
            is_new
            and not signal.matched_doc_type
            and self._soft_size_continuation(signal)
            and "+force_new" not in score_reason
        ):
            is_new = False
            score_reason += "; -soft_size_block_heuristic_new"

        # Trang đầu tiên không blank: nếu chưa match cũng mở group tạm
        if self._current_group is None and not self._groups and not self._orphan_pages:
            if is_new or signal.has_large_centered_text or score >= self.boundary_threshold:
                is_new = True

        if is_new:
            self._open_new_group(signal, score, score_reason)
            page_class = PageClass.NEW_DOCUMENT
            reasoning = f"new | {score_reason}"
        elif "repeated_catalog_header_same_type" in score_reason:
            page_class, reasoning = self._continue_or_new_or_orphan(
                signal, score, score_reason, "repeated_catalog_header_same_type"
            )
        elif self._soft_size_continuation(signal):
            page_class, reasoning = self._continue_or_new_or_orphan(
                signal, score, score_reason, "soft_same_size_group"
            )
        else:
            open_doc_type = (
                self._current_group.doc_type if self._current_group else None
            )
            open_pages = (
                len(self._current_group.page_numbers) if self._current_group else 0
            )
            max_soft = soft_max_pages_for(open_doc_type)
            at_soft_max = (
                self._current_group is not None
                and max_soft is not None
                and open_pages >= max_soft
            )
            verdict = self.validator.validate(
                self._prev_signal,
                signal,
                self.llm_referee,
                has_open_group=self._current_group is not None,
                open_doc_type=open_doc_type,
                open_page_count=open_pages,
            )
            if verdict.is_continuation or at_soft_max:
                page_class, reasoning = self._continue_or_new_or_orphan(
                    signal,
                    score,
                    score_reason,
                    verdict.reason if verdict.is_continuation else "open_at_soft_max",
                )
            else:
                self._mark_orphan(signal, verdict.reason)
                page_class = PageClass.ORPHAN_PAGE
                reasoning = f"orphan[{verdict.rule}] | {verdict.reason}"

        decision = BoundaryDecision(
            page_num=signal.page_num,
            page_class=page_class,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
        )

        if confidence == "low":
            self._low_confidence_pages.append(
                {
                    "page_num": signal.page_num,
                    "score": score,
                    "reasoning": reasoning,
                    "header_preview": signal.header_text[:200],
                    "matched_keyword": signal.matched_keyword,
                    "matched_doc_type": signal.matched_doc_type,
                    "page_class": page_class.value,
                    "page_size_group": signal.page_size_group,
                }
            )

        self._prev_signal = signal
        self._prev_was_blank = False
        return decision

    def update_current_group_meta(
        self, doc_type: str, doc_year: Optional[int]
    ) -> None:
        if self._current_group is not None:
            self._current_group.doc_type = doc_type
            if doc_year is not None:
                self._current_group.doc_year = doc_year

    def finalize(self) -> tuple[list[DocumentGroup], list[int]]:
        """Đóng group cuối; trả (groups hợp lệ, orphan page numbers)."""
        if self._current_group is not None:
            self._groups.append(self._current_group)
            logger.info(
                f"Closed final document #{self._current_group.group_id} "
                f"({len(self._current_group.page_numbers)} pages)"
            )
            self._current_group = None

        logger.info(
            f"Finalize: {len(self._groups)} documents, "
            f"{len(self._orphan_pages)} orphan pages, "
            f"{len(self._blank_pages)} blank pages"
        )
        return list(self._groups), list(self._orphan_pages)

    def get_documents(self) -> list[DocumentGroup]:
        """Trả về danh sách các document group đã và đang tạo."""
        res = list(self._groups)
        if self._current_group is not None:
            res.append(self._current_group)
        return res

    @property
    def current_group(self) -> DocumentGroup | None:
        return self._current_group

    @property
    def orphan_pages(self) -> list[int]:
        return list(self._orphan_pages)

    @property
    def blank_pages(self) -> list[int]:
        return list(self._blank_pages)

    @property
    def low_confidence_pages(self) -> list[dict]:
        return list(self._low_confidence_pages)
