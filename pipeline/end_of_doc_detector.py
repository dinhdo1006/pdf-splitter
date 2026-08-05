"""
pipeline/end_of_doc_detector.py
================================
Phát hiện tín hiệu kết thúc tài liệu (chữ ký / con dấu / text thưa cuối trang).

Dùng để boost NEW cho trang TIẾP THEO — không merge mù trang hiện tại.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from loguru import logger
from unidecode import unidecode

import config

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


@dataclass
class EndOfDocSignal:
    has_signature_label: bool = False
    has_circular_stamp: bool = False
    bottom_text_sparse: bool = False
    confidence: float = 0.0
    is_end_of_doc: bool = False


_SIGNATURE_LABELS = [
    r"giam\s*doc",
    r"bi\s*thu",
    r"chu\s*tich",
    r"truong\s*phong",
    r"nguoi\s*khai",
    r"nguoi\s*tu\s*kiem\s*diem",
    r"xac\s*nhan",
    r"t/?m\s+\w",
    r"thay\s*mat",
    r"pho\s*bi\s*thu",
    r"uy\s*vien",
    r"nguoi\s*nhan\s*ho\s*so",
    r"nguoi\s*giao\s*ho\s*so",
    r"ky\s*ten",
    r"chu\s*ky",
]


def _block_text(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("text", "") or "")
    return str(getattr(block, "text", "") or "")


def _block_bbox(block: Any) -> tuple[float, float, float, float]:
    if isinstance(block, dict):
        bb = block.get("bbox", (0, 0, 0, 0))
    else:
        bb = getattr(block, "bbox", (0, 0, 0, 0))
    if bb is None or len(bb) < 4:
        return 0.0, 0.0, 0.0, 0.0
    return float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])


def detect_end_of_doc(
    page_image: np.ndarray | None,
    ocr_blocks: Sequence[Any],
    page_height_px: int,
) -> EndOfDocSignal:
    """
    Trả về EndOfDocSignal. Exception nội bộ được nuốt — không crash pipeline.
    """
    signal = EndOfDocSignal()
    try:
        if page_height_px <= 0:
            return signal

        bottom_threshold = getattr(config, "EOD_BOTTOM_THRESHOLD", 0.72)
        bottom_y = page_height_px * bottom_threshold

        bottom_blocks = []
        for b in ocr_blocks or []:
            _x0, y0, _x1, _y1 = _block_bbox(b)
            if y0 >= bottom_y:
                bottom_blocks.append(b)

        all_bottom_text = unidecode(
            " ".join(_block_text(b) for b in bottom_blocks)
        ).lower()
        for pattern in _SIGNATURE_LABELS:
            if re.search(pattern, all_bottom_text, re.IGNORECASE):
                signal.has_signature_label = True
                break

        bottom_char_count = sum(len(_block_text(b)) for b in bottom_blocks)
        total_char_count = sum(len(_block_text(b)) for b in (ocr_blocks or []))
        if total_char_count > 0:
            bottom_ratio = bottom_char_count / total_char_count
            signal.bottom_text_sparse = bottom_ratio < 0.20

        if (
            page_image is not None
            and cv2 is not None
            and isinstance(page_image, np.ndarray)
            and page_image.size > 0
        ):
            try:
                gray = (
                    cv2.cvtColor(page_image, cv2.COLOR_RGB2GRAY)
                    if page_image.ndim == 3
                    else page_image
                )
                y0 = int(page_height_px * bottom_threshold)
                bottom_region = gray[y0:, :]
                if bottom_region.size > 0:
                    blurred = cv2.GaussianBlur(bottom_region, (9, 9), 2)
                    circles = cv2.HoughCircles(
                        blurred,
                        cv2.HOUGH_GRADIENT,
                        dp=1.2,
                        minDist=30,
                        param1=50,
                        param2=30,
                        minRadius=20,
                        maxRadius=80,
                    )
                    if circles is not None:
                        signal.has_circular_stamp = True
            except Exception as exc:
                logger.debug(f"[eod] stamp detect skipped: {exc}")

        positive = sum(
            [
                signal.has_signature_label,
                signal.has_circular_stamp,
                signal.bottom_text_sparse,
            ]
        )
        if positive >= 2:
            signal.confidence = 0.85
            signal.is_end_of_doc = True
        elif positive == 1 and signal.has_signature_label:
            signal.confidence = 0.65
            signal.is_end_of_doc = True
        else:
            signal.confidence = 0.20
            signal.is_end_of_doc = False

    except Exception as exc:
        logger.warning(f"[eod] detect_end_of_doc failed: {exc}")
        return EndOfDocSignal()

    return signal
