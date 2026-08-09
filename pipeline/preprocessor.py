"""Module 2: Deskew + image enhancement for better OCR accuracy."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

import config


class PagePreprocessor:
    """Fix skewed/rotated scanned pages before OCR."""

    def __init__(
        self,
        max_skew_angle: float = config.MAX_SKEW_ANGLE,
        skew_threshold: float = config.SKEW_THRESHOLD,
    ) -> None:
        """
        max_skew_angle: maximum angle to search for deskewing (degrees)
        skew_threshold: only rotate if detected angle > this value (degrees)
        """
        self.max_skew_angle = max_skew_angle
        self.skew_threshold = skew_threshold

    def deskew(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        """
        Detect skew angle using Hough Line Transform on edges.
        If angle detection fails, return (original_image, 0.0).
        """
        try:
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            else:
                gray = image.copy()

            edges = cv2.Canny(gray, 50, 150, apertureSize=3)
            lines = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi / 180,
                threshold=100,
                minLineLength=gray.shape[1] // 4,
                maxLineGap=20,
            )

            if lines is None or len(lines) == 0:
                return image, 0.0

            angles: list[float] = []
            for line in lines:
                pts = np.asarray(line).reshape(-1)
                if pts.size < 4:
                    continue
                x1, y1, x2, y2 = (int(pts[0]), int(pts[1]), int(pts[2]), int(pts[3]))
                dx = x2 - x1
                dy = y2 - y1
                if dx == 0:
                    continue
                angle = np.degrees(np.arctan2(dy, dx))
                # Keep near-horizontal lines within search window
                if abs(angle) <= self.max_skew_angle:
                    angles.append(float(angle))
                elif abs(angle) >= (90 - self.max_skew_angle):
                    # Near-vertical — convert to skew relative to vertical
                    adjusted = angle - 90.0 if angle > 0 else angle + 90.0
                    if abs(adjusted) <= self.max_skew_angle:
                        angles.append(float(adjusted))

            if not angles:
                return image, 0.0

            median_angle = float(np.median(angles))

            if abs(median_angle) <= self.skew_threshold:
                return image, median_angle

            # Rotate to correct skew (negative of detected angle)
            (h, w) = image.shape[:2]
            center = (w // 2, h // 2)
            rot_matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
            corrected = cv2.warpAffine(
                image,
                rot_matrix,
                (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
            return corrected, median_angle

        except Exception as exc:
            logger.warning(f"Deskew failed: {exc}")
            return image, 0.0

    def enhance_contrast(self, image: np.ndarray) -> np.ndarray:
        """
        Improve OCR accuracy on low-quality scans.
        CLAHE + slight Gaussian blur.
        """
        try:
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            else:
                gray = image.copy()

            clahe = cv2.createCLAHE(
                clipLimit=config.CLAHE_CLIP_LIMIT,
                tileGridSize=config.CLAHE_TILE_SIZE,
            )
            enhanced = clahe.apply(gray)
            blurred = cv2.GaussianBlur(enhanced, (3, 3), 0)
            return blurred
        except Exception as exc:
            logger.warning(f"Contrast enhancement failed: {exc}")
            if len(image.shape) == 3:
                return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            return image

    def process(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        """
        Full preprocessing pipeline: deskew → enhance_contrast.
        Return (processed_image, skew_angle).
        """
        deskewed, angle = self.deskew(image)
        enhanced = self.enhance_contrast(deskewed)
        return enhanced, angle


# Quick test
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.preprocessor <image.png|path/to.pdf>")
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
        image = cv2.imread(str(src))
        if image is None:
            print(f"Cannot read image: {src}")
            sys.exit(1)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    preprocessor = PagePreprocessor()
    processed, angle = preprocessor.process(image)
    out_path = Path("logs") / "deskew_test.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), processed)
    print(f"Skew angle: {angle:.2f}° → saved {out_path}")
