"""Module 4: Extract boundary signals per page from OCR output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from loguru import logger

import config
from pipeline.end_of_doc_detector import detect_end_of_doc
from pipeline.ocr_engine import OCRBlock, OCREngine
from pipeline.party_doc_matcher import PartyDocMatcher, get_matcher

# Patterns indicating "this is a continuation page, NOT a new document"
CONTINUATION_PATTERNS = [
    r"trang\s+\d+\s*/\s*\d+",  # "Trang 2/5"
    r"page\s+\d+\s+of\s+\d+",  # "Page 2 of 5"
    r"\(tiếp\s+theo\)",  # "(tiếp theo)"
    r"\(tiep\s+theo\)",
    r"^-\s*\d+\s*-$",  # "- 2 -" (page number only)
    r"^\d+$",  # bare number as sole header content
]

# Kept for TitleNormalizer line scoring — phrases from catalog aliases only
DOCUMENT_KEYWORDS = [
    "LÝ LỊCH ĐẢNG VIÊN",
    "LÝ LỊCH",
    "SƠ YẾU LÝ LỊCH",
    "PHIẾU BỔ SUNG",
    "PHIẾU ĐẢNG VIÊN",
    "BẢN TỰ KIỂM ĐIỂM",
    "BẢN KIỂM ĐIỂM",
    "GIẤY GIỚI THIỆU SINH HOẠT ĐẢNG",
    "QUYẾT ĐỊNH KẾT NẠP",
    "QUYẾT ĐỊNH CÔNG NHẬN",
    "ĐƠN XIN VÀO ĐẢNG",
    "MẪU 3-HSĐV",
]


@dataclass
class PageSignal:
    page_num: int
    header_text: str
    header_blocks: list
    full_text: str
    has_doc_keyword: bool
    matched_keyword: str
    has_large_centered_text: bool
    is_continuation: bool
    text_density: float
    is_blank: bool
    avg_confidence: float
    matched_doc_type: str = ""
    match_score: float = 0.0
    all_blocks: list = field(default_factory=list)
    is_toc: bool = False
    is_form_section: bool = False
    # Page size (pt) — gold signal, không phụ thuộc OCR/DPI
    page_width_pt: float = 0.0
    page_height_pt: float = 0.0
    page_size_group: str = "OTHER"
    ocr_dpi_recommended: int = 200
    boundary_score: float = 0.0
    # End-of-document
    end_of_doc_confidence: float = 0.0
    is_likely_end_of_doc: bool = False
    # Soft appendix (Mẫu 2a, biên bản…) — không phải catalog 104
    is_appendix: bool = False
    appendix_kind: str = ""


def _empty_signal(page_num: int) -> PageSignal:
    return PageSignal(
        page_num=page_num,
        header_text="",
        header_blocks=[],
        full_text="",
        has_doc_keyword=False,
        matched_keyword="",
        has_large_centered_text=False,
        is_continuation=False,
        text_density=0.0,
        is_blank=True,
        avg_confidence=0.0,
        matched_doc_type="",
        match_score=0.0,
        all_blocks=[],
        is_toc=False,
        is_form_section=False,
    )


class SignalExtractor:
    """Analyze OCR output and produce boundary signals for one page."""

    def __init__(
        self,
        ocr_engine: OCREngine,
        matcher: PartyDocMatcher | None = None,
    ) -> None:
        self.ocr = ocr_engine
        self.matcher = matcher or get_matcher()

    def _match_catalog(
        self,
        header_text: str,
        full_text: str = "",
        page_size_group: str = "OTHER",
    ) -> tuple[bool, str, str, float, bool, bool, bool, str]:
        """
        Returns:
            (has_hit, display_phrase, doc_type_key, score,
             is_toc, is_form_section, is_appendix, appendix_kind)
        """
        result = self.matcher.match(
            header_text, full_text, page_size_group=page_size_group
        )
        is_toc = result.source == "toc"
        is_form = result.source == "form_section"
        is_appendix = result.source == "appendix"
        appendix_kind = result.matched_phrase if is_appendix else ""
        min_score = getattr(config, "CATALOG_MATCH_MIN_SCORE", 82)
        if result.doc_type_key and result.score >= min_score:
            return (
                True,
                result.matched_phrase,
                result.doc_type_key,
                result.score,
                False,
                False,
                False,
                "",
            )
        return (
            False,
            result.matched_phrase,
            "",
            0.0,
            is_toc,
            is_form,
            is_appendix,
            appendix_kind,
        )

    def _has_large_centered_text(
        self,
        header_blocks: list[OCRBlock],
        image_height: int,
        image_width: int,
    ) -> bool:
        if image_height <= 0 or image_width <= 0:
            return False

        x_lo, x_hi = config.CENTER_TEXT_X_RANGE
        min_height = image_height * config.LARGE_FONT_HEIGHT_RATIO

        for block in header_blocks:
            x_min, y_min, x_max, y_max = block.bbox
            h = max(0, y_max - y_min)
            if h < min_height or block.confidence < 0.6:
                continue
            center_x = (x_min + x_max) / 2.0 / image_width
            if x_lo <= center_x <= x_hi:
                return True
        return False

    def _is_continuation(self, header_text: str) -> bool:
        text = (header_text or "").strip()
        if not text:
            return False
        for pat in CONTINUATION_PATTERNS:
            if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
                return True
        return False

    def _text_density(
        self, blocks: list[OCRBlock], image_height: int, image_width: int
    ) -> float:
        page_area = max(1, image_height * image_width)
        total = 0.0
        for block in blocks:
            x_min, y_min, x_max, y_max = block.bbox
            total += max(0, x_max - x_min) * max(0, y_max - y_min)
        return float(min(1.0, max(0.0, total / page_area)))

    def extract(
        self,
        page_num: int,
        image: np.ndarray,
        handwriting_ocr: Any = None,
        handwriting_detector: Any = None,
        width_pt: float = 0.0,
        height_pt: float = 0.0,
    ) -> PageSignal:
        try:
            if len(image.shape) == 2:
                image_height, image_width = image.shape
            else:
                image_height, image_width = image.shape[:2]

            size_group = (
                config.classify_page_size(width_pt, height_pt)
                if width_pt > 0 and height_pt > 0
                else "OTHER"
            )
            dpi_rec = (
                config.page_size_ocr_dpi(width_pt, height_pt)
                if width_pt > 0 and height_pt > 0
                else config.PDF_RENDER_DPI
            )

            printed_blocks, hw_blocks = self.ocr.run_dual(
                image, handwriting_ocr, handwriting_detector
            )
            all_blocks = printed_blocks

            header_blocks = self.ocr.extract_zone(
                all_blocks,
                zone_top=config.HEADER_ZONE_TOP,
                zone_bottom=config.HEADER_ZONE_BOTTOM,
            )
            header_text = self.ocr.blocks_to_text(header_blocks).upper()
            full_text = self.ocr.blocks_to_text(all_blocks)

            if hw_blocks:
                hw_header = [
                    b
                    for b in hw_blocks
                    if (b.bbox[1] + b.bbox[3] / 2.0) / max(image_height, 1)
                    < config.HEADER_ZONE_BOTTOM
                ]
                if hw_header:
                    hw_text = " ".join(b.text for b in hw_header)
                    header_text = (header_text + " " + hw_text).strip().upper()
                    logger.debug(
                        f"Page {page_num}: merged {len(hw_header)} HW header blocks"
                    )

                hw_all = " ".join(b.text for b in hw_blocks)
                if hw_all:
                    full_text = (full_text + "\n" + hw_all).strip()

            (
                has_kw,
                matched_kw,
                doc_type,
                match_score,
                is_toc,
                is_form,
                is_appendix,
                appendix_kind,
            ) = self._match_catalog(header_text, full_text, size_group)
            has_large_centered = self._has_large_centered_text(
                header_blocks, image_height, image_width
            )
            is_continuation = self._is_continuation(header_text)
            density = self._text_density(all_blocks, image_height, image_width)
            is_blank = density < config.BLANK_PAGE_DENSITY

            if header_blocks:
                avg_conf = float(
                    sum(b.confidence for b in header_blocks) / len(header_blocks)
                )
            else:
                avg_conf = 0.0

            # Size-group doc_type hint khi chưa match catalog (chỉ strong groups)
            if (
                not has_kw
                and not is_toc
                and not is_form
                and size_group in getattr(config, "STRONG_SIZE_CONTINUATION_GROUPS", ())
            ):
                hint = config.PAGE_SIZE_GROUPS.get(size_group, {}).get("doc_type_hint")
                # Không ép has_doc_keyword — chỉ gợi ý khi mở group đầu tiên trong group size
                # BoundaryDetector dùng page_size_group; hint optional qua matched nếu trống
                _ = hint  # reserved; soft cont dùng size group, không fake catalog hit

            eod = detect_end_of_doc(image, all_blocks, image_height)

            return PageSignal(
                page_num=page_num,
                header_text=header_text,
                header_blocks=header_blocks,
                full_text=full_text,
                has_doc_keyword=has_kw,
                matched_keyword=matched_kw,
                has_large_centered_text=has_large_centered,
                is_continuation=is_continuation,
                text_density=density,
                is_blank=is_blank,
                avg_confidence=avg_conf,
                matched_doc_type=doc_type,
                match_score=match_score,
                all_blocks=list(all_blocks),
                is_toc=is_toc,
                is_form_section=is_form,
                page_width_pt=float(width_pt or 0.0),
                page_height_pt=float(height_pt or 0.0),
                page_size_group=size_group,
                ocr_dpi_recommended=int(dpi_rec),
                end_of_doc_confidence=eod.confidence,
                is_likely_end_of_doc=eod.is_end_of_doc,
                is_appendix=is_appendix,
                appendix_kind=appendix_kind,
            )

        except Exception as exc:
            logger.warning(f"Signal extraction failed for page {page_num}: {exc}")
            return _empty_signal(page_num)
