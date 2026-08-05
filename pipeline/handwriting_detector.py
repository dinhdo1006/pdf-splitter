"""Handwriting region detection via image-processing heuristics (CPU-only)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

import config


class HandwritingDetector:
    """
    Phân biệt vùng printed vs handwritten dựa trên đặc điểm hình ảnh.
    Không dùng ML — dùng image processing thuần để chạy nhanh trên CPU.
    """

    def __init__(
        self,
        stroke_irregularity_threshold: float = config.HW_STROKE_THRESHOLD,
        min_region_area: int = config.HW_MIN_REGION_AREA,
        merge_distance: int = 20,
    ) -> None:
        """
        stroke_irregularity_threshold:
            Vùng có contour irregularity score > ngưỡng này → nghi là viết tay.
        min_region_area: bỏ qua vùng quá nhỏ (nhiễu, chấm, dấu)
        merge_distance: gộp bbox gần nhau hơn khoảng cách này (px)
        """
        self.stroke_threshold = stroke_irregularity_threshold
        self.min_area = min_region_area
        self.merge_distance = merge_distance

    def _irregularity_score(self, contour: np.ndarray) -> float:
        """
        Đo độ không đều của một contour.
        Score = 1 - (4π × Area) / Perimeter²
        """
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if perimeter == 0:
            return 0.0
        circularity = (4 * np.pi * area) / (perimeter**2)
        return float(1.0 - min(circularity, 1.0))

    def _merge_bboxes(
        self,
        boxes: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        """Merge bboxes within merge_distance of each other (xywh)."""
        if not boxes:
            return []

        # Convert to x1,y1,x2,y2 for easier overlap checks
        rects = [[x, y, x + w, y + h] for x, y, w, h in boxes]
        merged = True
        while merged:
            merged = False
            new_rects: list[list[int]] = []
            used = [False] * len(rects)

            for i in range(len(rects)):
                if used[i]:
                    continue
                x1, y1, x2, y2 = rects[i]
                used[i] = True
                for j in range(i + 1, len(rects)):
                    if used[j]:
                        continue
                    a1, b1, a2, b2 = rects[j]
                    # Expand rect i by merge_distance and test intersection
                    if (
                        x1 - self.merge_distance <= a2
                        and a1 - self.merge_distance <= x2
                        and y1 - self.merge_distance <= b2
                        and b1 - self.merge_distance <= y2
                    ):
                        x1 = min(x1, a1)
                        y1 = min(y1, b1)
                        x2 = max(x2, a2)
                        y2 = max(y2, b2)
                        used[j] = True
                        merged = True
                new_rects.append([x1, y1, x2, y2])
            rects = new_rects

        return [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in rects]

    def detect_handwritten_regions(
        self,
        image: np.ndarray,
    ) -> list[tuple[int, int, int, int]]:
        """
        Trả về list các bbox (x, y, w, h) nghi là viết tay.
        False positive (printed → handwritten) ổn hơn false negative.
        """
        try:
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            else:
                gray = image.copy()

            binary = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                15,
                8,
            )

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

            contours, _ = cv2.findContours(
                closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            hw_boxes: list[tuple[int, int, int, int]] = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < self.min_area:
                    continue
                score = self._irregularity_score(contour)
                if score > self.stroke_threshold:
                    x, y, w, h = cv2.boundingRect(contour)
                    hw_boxes.append((int(x), int(y), int(w), int(h)))

            return self._merge_bboxes(hw_boxes)

        except Exception as exc:
            logger.warning(f"Handwriting detection failed: {exc}")
            return []

    def create_mask(
        self,
        image: np.ndarray,
        regions: list[tuple[int, int, int, int]],
    ) -> np.ndarray:
        """Binary mask: 255 ở vùng handwritten, 0 ở vùng printed."""
        h, w = image.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)
        for x, y, bw, bh in regions:
            x1 = max(0, x)
            y1 = max(0, y)
            x2 = min(w, x + bw)
            y2 = min(h, y + bh)
            mask[y1:y2, x1:x2] = 255
        return mask

    def split_image(
        self,
        image: np.ndarray,
        regions: list[tuple[int, int, int, int]],
    ) -> tuple[np.ndarray, list[tuple[np.ndarray, tuple[int, int, int, int]]]]:
        """
        Tách image thành:
        - printed_image: vùng handwritten bị fill trắng
        - handwritten_crops: list of (cropped_image, original_bbox)
        """
        printed = image.copy()
        crops: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
        h, w = image.shape[:2]

        for x, y, bw, bh in regions:
            x1 = max(0, x)
            y1 = max(0, y)
            x2 = min(w, x + bw)
            y2 = min(h, y + bh)
            if x2 <= x1 or y2 <= y1:
                continue

            crop = image[y1:y2, x1:x2].copy()
            crops.append((crop, (x1, y1, x2 - x1, y2 - y1)))

            # Fill handwritten region with white on printed image
            if len(printed.shape) == 2:
                printed[y1:y2, x1:x2] = 255
            else:
                printed[y1:y2, x1:x2] = 255

        return printed, crops


# Quick test
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.handwriting_detector <image.png|pdf>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src}")
        sys.exit(1)

    if src.suffix.lower() == ".pdf":
        import fitz

        doc = fitz.open(str(src))
        page = doc.load_page(0)
        zoom = 150 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            image = image[:, :, :3].copy()
        else:
            image = image.copy()
        doc.close()
    else:
        bgr = cv2.imread(str(src))
        if bgr is None:
            print(f"Cannot read image: {src}")
            sys.exit(1)
        image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    detector = HandwritingDetector()
    regions = detector.detect_handwritten_regions(image)
    print(f"Detected {len(regions)} handwritten regions")
    for i, (x, y, w, h) in enumerate(regions[:20]):
        print(f"  [{i}] x={x} y={y} w={w} h={h}")

    # Visualize
    vis = image.copy() if len(image.shape) == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    for x, y, w, h in regions:
        cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 0, 0), 2)
    out = Path(config.LOG_DIR) / "hw_detect_test.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(vis, cv2.COLOR_RGB2BGR))
    print(f"Saved visualization → {out}")
