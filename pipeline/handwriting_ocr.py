"""TrOCR wrapper for handwritten text recognition (local, CPU-friendly)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

import config

_TRANSFORMERS_AVAILABLE = False
try:
    import torch
    from PIL import Image
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]
    TrOCRProcessor = None  # type: ignore[assignment,misc]
    VisionEncoderDecoderModel = None  # type: ignore[assignment,misc]


@dataclass
class HandwrittenBlock:
    text: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h) trong ảnh gốc
    source: str = "trocr"


class HandwritingOCR:
    """
    Wrapper cho Microsoft TrOCR — đọc chữ viết tay từ ảnh crop.

    Model: microsoft/trocr-base-handwritten
    - Chạy được trên CPU
    - Không cần internet sau lần tải đầu tiên
    """

    MODEL_NAME = config.TROCR_MODEL_NAME

    def __init__(
        self,
        use_gpu: bool = config.TROCR_USE_GPU,
        cache_dir: str = config.TROCR_CACHE_DIR,
    ) -> None:
        """
        Load TrOCRProcessor và VisionEncoderDecoderModel một lần duy nhất.
        """
        self.use_gpu = use_gpu
        self.cache_dir = cache_dir
        self.processor = None
        self.model = None
        self.device = "cpu"
        self._restorer = None
        self._available = False

        if not _TRANSFORMERS_AVAILABLE:
            logger.warning(
                "transformers/torch/Pillow chưa cài — HandwritingOCR không khả dụng"
            )
            return

        Path(cache_dir).mkdir(parents=True, exist_ok=True)

        try:
            t0 = time.perf_counter()
            if use_gpu and torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"

            self.processor = TrOCRProcessor.from_pretrained(
                self.MODEL_NAME,
                cache_dir=cache_dir,
                use_fast=False,
            )
            self.model = VisionEncoderDecoderModel.from_pretrained(
                self.MODEL_NAME,
                cache_dir=cache_dir,
            )
            self.model.to(self.device)
            self.model.eval()
            self._available = True

            from pipeline.text_restorer import VietnameseTextRestorer

            self._restorer = VietnameseTextRestorer()

            elapsed = time.perf_counter() - t0
            logger.info(
                f"TrOCR loaded ({self.MODEL_NAME}) on {self.device} in {elapsed:.1f}s"
            )
        except Exception as exc:
            logger.error(f"Failed to load TrOCR: {exc}")
            self._available = False

    def is_available(self) -> bool:
        """Kiểm tra transformers + torch đã sẵn sàng và model đã load."""
        return self._available and self.model is not None and self.processor is not None

    @staticmethod
    def _to_pil(crop: np.ndarray) -> "Image.Image":
        """Convert numpy crop → RGB PIL Image, resize height to 64px."""
        if len(crop.shape) == 2:
            pil = Image.fromarray(crop).convert("RGB")
        elif crop.shape[2] == 4:
            pil = Image.fromarray(crop[:, :, :3]).convert("RGB")
        else:
            pil = Image.fromarray(crop).convert("RGB")

        target_h = 64
        w, h = pil.size
        if h > 0 and h != target_h:
            new_w = max(1, int(round(w * (target_h / h))))
            pil = pil.resize((new_w, target_h), Image.Resampling.LANCZOS)
        return pil

    @staticmethod
    def _is_meaningful(text: str) -> bool:
        """Reject empty or special-character-only strings."""
        cleaned = text.strip()
        if not cleaned:
            return False
        if not re.search(r"[A-Za-z0-9À-ỹ]", cleaned):
            return False
        return True

    def recognize(
        self,
        crops: list[tuple[np.ndarray, tuple[int, int, int, int]]],
    ) -> list[HandwrittenBlock]:
        """
        Nhận list (cropped_image_numpy, original_bbox).
        Batch theo TROCR_BATCH_SIZE. Áp dụng VietnameseTextRestorer sau decode.
        """
        if not self.is_available() or not crops:
            return []

        assert self.processor is not None and self.model is not None
        results: list[HandwrittenBlock] = []
        batch_size = config.TROCR_BATCH_SIZE

        try:
            for start in range(0, len(crops), batch_size):
                batch = crops[start : start + batch_size]
                pil_images = [self._to_pil(crop) for crop, _ in batch]
                bboxes = [bbox for _, bbox in batch]

                pixel_values = self.processor(
                    images=pil_images,
                    return_tensors="pt",
                ).pixel_values.to(self.device)

                with torch.no_grad():
                    generated_ids = self.model.generate(
                        pixel_values,
                        max_new_tokens=64,
                    )

                texts = self.processor.batch_decode(
                    generated_ids,
                    skip_special_tokens=True,
                )

                for text, bbox in zip(texts, bboxes):
                    raw = (text or "").strip()
                    if not self._is_meaningful(raw):
                        continue
                    if self._restorer is not None:
                        restored = self._restorer.restore(raw)
                    else:
                        restored = raw
                    results.append(
                        HandwrittenBlock(text=restored, bbox=bbox, source="trocr")
                    )
                    logger.debug(f"TrOCR: {raw!r} → {restored!r} @ {bbox}")

        except Exception as exc:
            logger.warning(f"TrOCR recognize failed: {exc}")

        return results


# Quick test
if __name__ == "__main__":
    import sys

    import cv2

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.handwriting_ocr <crop_image.png>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src}")
        sys.exit(1)

    bgr = cv2.imread(str(src))
    if bgr is None:
        print(f"Cannot read image: {src}")
        sys.exit(1)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]

    ocr = HandwritingOCR()
    if not ocr.is_available():
        print("TrOCR not available — install torch + transformers + Pillow")
        sys.exit(1)

    blocks = ocr.recognize([(rgb, (0, 0, w, h))])
    print(f"Recognized {len(blocks)} block(s):")
    for b in blocks:
        print(f"  {b.text!r}  bbox={b.bbox}")
