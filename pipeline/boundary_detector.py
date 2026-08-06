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
from pipeline.continuation_validator import ContinuationValidator, MULTI_PAGE_FORM_TYPES
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

        self._group_counter += 1
        self._current_group = DocumentGroup(
            group_id=self._group_counter,
            raw_title=_clean_header(signal.header_text),
            doc_type=doc_type,
            doc_year=None,
            page_numbers=[signal.page_num],
            page_size_group=signal.page_size_group or "OTHER",
        )
        logger.info(
            f"NEW_DOCUMENT #{self._group_counter} at page {signal.page_num} "
            f"(score={score:.2f}, doc_type={doc_type!r}, "
            f"size={signal.page_size_group}) | {reason}"
        )

    def _append_continuation(self, signal: PageSignal, reason: str) -> bool:
        if self._current_group is None:
            return False
        self._current_group.page_numbers.append(signal.page_num)
        logger.debug(
            f"Page {signal.page_num}: CONFIRMED_CONTINUATION → "
            f"group #{self._current_group.group_id} | {reason}"
        )
        return True

    def _mark_orphan(self, signal: PageSignal, reason: str) -> None:
        """
        Cách ly trang mồ côi — KHÔNG đóng group đang mở.
        Orphan không bị gộp; group vẫn mở để trang sau còn
        CONFIRMED_CONTINUATION (tránh cắt ngang lý lịch nhiều trang).
        """
        self._orphan_pages.append(signal.page_num)
        open_id = self._current_group.group_id if self._current_group else None
        logger.warning(
            f"Page {signal.page_num}: ORPHAN_PAGE (group #{open_id} vẫn mở) — {reason}"
        )

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
                # Biên bản / nghị quyết độc lập → orphan, không nuốt vào lý lịch
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

        # TOC: LUÔN orphan — không bao giờ gộp vào lý lịch / soft size
        if getattr(signal, "is_toc", False):
            # Đóng booklet/landscape đang mở trước khi orphan TOC
            if (
                self._current_group is not None
                and self._current_group.page_size_group in _STRONG_SIZE
            ):
                self._close_current_group("toc_closes_booklet_group")
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

        # Mục form lý lịch (22), 23)…): chỉ gộp khi đang mở LL CÙNG size group
        if getattr(signal, "is_form_section", False):
            if (
                is_ly_lich_open
                and self._soft_size_continuation(signal)
                and self._append_continuation(signal, "form_section_inside_ly_lich")
            ):
                decision = BoundaryDecision(
                    page_num=signal.page_num,
                    page_class=PageClass.CONFIRMED_CONTINUATION,
                    score=score,
                    confidence=confidence,
                    reasoning=f"confirmed_cont[form_section_ly_lich] | {score_reason}",
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

        # Form nhiều trang: tiêu đề catalog lặp lại trên trang tiếp → không NEW
        # Ngoại lệ: đổi page_size_group (vd. landscape sơ yếu → booklet lý lịch)
        if (
            is_new
            and self._current_group is not None
            and signal.matched_doc_type
            and signal.matched_doc_type == self._current_group.doc_type
            and signal.matched_doc_type in MULTI_PAGE_FORM_TYPES
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
        ):
            is_new = False
            score_reason += "; -soft_size_block_heuristic_new"

        # Trang đầu tiên không blank: nếu chưa match cũng mở group tạm
        if self._current_group is None and not self._groups and not self._orphan_pages:
            if is_new or signal.has_large_centered_text or score >= self.boundary_threshold:
                is_new = True

        if is_new:
            self._open_new_group(signal, score, score_reason)
            if self._current_group and self._current_group.doc_year is None:
                y = extract_year_robust(
                    signal.header_text + "\n" + (signal.full_text or "")[:300]
                )
                if y is not None:
                    self._current_group.doc_year = y
            page_class = PageClass.NEW_DOCUMENT
            reasoning = f"new | {score_reason}"
        elif "repeated_catalog_header_same_type" in score_reason:
            if self._append_continuation(signal, "repeated_catalog_header_same_type"):
                page_class = PageClass.CONFIRMED_CONTINUATION
                reasoning = f"confirmed_cont[same_type_header] | {score_reason}"
            else:
                self._mark_orphan(signal, "repeated_header_but_no_open_group")
                page_class = PageClass.ORPHAN_PAGE
                reasoning = "orphan | no_open_group"
        elif self._soft_size_continuation(signal):
            if self._append_continuation(signal, "soft_same_size_group"):
                page_class = PageClass.CONFIRMED_CONTINUATION
                reasoning = f"confirmed_cont[soft_size] | {score_reason}"
            else:
                self._mark_orphan(signal, "soft_size_but_no_open_group")
                page_class = PageClass.ORPHAN_PAGE
                reasoning = "orphan | no_open_group"
        else:
            open_doc_type = (
                self._current_group.doc_type if self._current_group else None
            )
            verdict = self.validator.validate(
                self._prev_signal,
                signal,
                self.llm_referee,
                has_open_group=self._current_group is not None,
                open_doc_type=open_doc_type,
            )
            if verdict.is_continuation:
                if self._append_continuation(signal, verdict.reason):
                    page_class = PageClass.CONFIRMED_CONTINUATION
                    reasoning = f"confirmed_cont[{verdict.rule}] | {verdict.reason}"
                else:
                    self._mark_orphan(
                        signal,
                        f"continuation_claimed_but_no_open_group | {verdict.reason}",
                    )
                    page_class = PageClass.ORPHAN_PAGE
                    reasoning = "orphan | no_open_group"
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
