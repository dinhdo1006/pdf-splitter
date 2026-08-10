"""Module 3: OCR with PaddleOCR (Vietnamese) — auto GPU/CPU."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from loguru import logger

import config


@dataclass
class OCRBlock:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max)
    center_y: float  # vertical center of bbox, normalized 0.0-1.0


def _normalize_gpu_pref(use_gpu: Union[bool, str, None]) -> str:
    """Chuẩn hóa preference → 'auto' | 'gpu' | 'cpu'."""
    if use_gpu is None:
        use_gpu = getattr(config, "OCR_USE_GPU", "auto")
    if isinstance(use_gpu, bool):
        return "gpu" if use_gpu else "cpu"
    s = str(use_gpu).strip().lower()
    if s in {"1", "true", "yes", "gpu"}:
        return "gpu"
    if s in {"0", "false", "no", "cpu"}:
        return "cpu"
    return "auto"


def _nvidia_free_mb() -> Optional[int]:
    """MB VRAM trống trên GPU 0 (nvidia-smi). None nếu không đọc được."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        first = (out or "").strip().splitlines()[0].strip()
        return int(re.sub(r"[^\d]", "", first) or "0")
    except Exception:
        return None


def detect_gpu_ready(
    min_free_mb: Optional[int] = None,
) -> tuple[bool, str]:
    """
    True nếu Paddle CUDA sẵn sàng và (nếu check được) còn đủ VRAM trống.
    """
    min_free = int(
        min_free_mb
        if min_free_mb is not None
        else getattr(config, "OCR_GPU_MIN_FREE_MB", 1500)
    )
    try:
        import paddle

        if not paddle.device.is_compiled_with_cuda():
            return False, "paddle_not_compiled_with_cuda"
        if paddle.device.cuda.device_count() <= 0:
            return False, "cuda_device_count_0"
    except Exception as exc:
        return False, f"paddle_cuda_check_failed:{exc}"

    free_mb = _nvidia_free_mb()
    if free_mb is not None and free_mb < min_free:
        return (
            False,
            f"vram_low free={free_mb}MB < min={min_free}MB (có thể Ollama đang chiếm)",
        )
    if free_mb is not None:
        return True, f"cuda_ok free_vram={free_mb}MB"
    return True, "cuda_ok"


def resolve_ocr_use_gpu(
    use_gpu: Union[bool, str, None] = None,
) -> tuple[bool, str]:
    """
    Quyết định có dùng GPU không.
    Returns (use_gpu: bool, reason: str).
    """
    pref = _normalize_gpu_pref(use_gpu)
    if pref == "cpu":
        return False, "forced_cpu"
    ready, detail = detect_gpu_ready()
    if ready:
        return True, f"prefer_{pref}:{detail}"
    if pref == "gpu":
        # Vẫn thử GPU — caller có thể fallback nếu init fail
        return True, f"forced_gpu_despite:{detail}"
    return False, f"auto_cpu:{detail}"


class OCREngine:
    """Run OCR on page images using PaddleOCR."""

    def __init__(
        self,
        lang: str = config.OCR_LANG,
        use_gpu: Union[bool, str, None] = None,
    ) -> None:
        """
        Initialize PaddleOCR.
        Models auto-download on first run.
        Compatible with PaddleOCR 3.x (device=...) and legacy 2.x kwargs.

        use_gpu: True/False/"auto"/None — None lấy từ config.OCR_USE_GPU.
        Có GPU thì dùng GPU; không có / thiếu VRAM / init fail → CPU.
        """
        if use_gpu is None:
            use_gpu = getattr(config, "OCR_USE_GPU", "auto")
        self.lang = lang
        self.ocr = None
        self._api_version = 3

        want_gpu, why = resolve_ocr_use_gpu(use_gpu)
        logger.info(f"[ocr] device preference → gpu={want_gpu} ({why})")

        # Thử GPU trước; fail → CPU (trừ khi đã chọn CPU sẵn)
        tried_gpu = False
        last_exc: Optional[BaseException] = None
        if want_gpu:
            tried_gpu = True
            try:
                self._init_paddle(lang=lang, use_gpu=True)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"[ocr] GPU init failed ({exc}) — fallback CPU"
                )

        try:
            self._init_paddle(lang=lang, use_gpu=False)
        except Exception as exc:
            if last_exc is not None:
                logger.error(f"[ocr] CPU fallback also failed after GPU error: {last_exc}")
            logger.error(f"Failed to initialize PaddleOCR: {exc}")
            raise

        if tried_gpu:
            logger.info("[ocr] running on CPU after GPU fallback")

    def _init_paddle(self, *, lang: str, use_gpu: bool) -> None:
        """Khởi tạo PaddleOCR trên device chỉ định (raise nếu fail)."""
        self.use_gpu = use_gpu

        # Tắt OneDNN/MKLDNN TRƯỚC khi import paddle
        os.environ["FLAGS_use_mkldnn"] = "0"
        os.environ["FLAGS_onednn"] = "0"
        os.environ["FLAGS_use_mkldnn_bfloat16"] = "0"
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        if use_gpu:
            # Đừng để env cũ ép trống GPU
            if os.environ.get("CUDA_VISIBLE_DEVICES", None) == "":
                os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""

        from paddleocr import PaddleOCR

        if use_gpu:
            import paddle

            if not paddle.device.is_compiled_with_cuda():
                raise RuntimeError("paddle not compiled with CUDA")
            if paddle.device.cuda.device_count() <= 0:
                raise RuntimeError("no CUDA device visible to paddle")

        try:
            import paddle

            paddle.set_flags({"FLAGS_use_mkldnn": False})
        except Exception:
            pass

        device = "gpu:0" if use_gpu else "cpu"

        kwargs_v3: dict[str, Any] = {
            "lang": lang,
            "use_textline_orientation": True,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "device": device,
            "enable_mkldnn": False,
        }

        det_dir = config.MODELS_DIR / "det"
        rec_dir = config.MODELS_DIR / "rec"
        cls_dir = config.MODELS_DIR / "cls"
        if det_dir.is_dir() and any(det_dir.iterdir()):
            kwargs_v3["text_detection_model_dir"] = str(det_dir)
        if rec_dir.is_dir() and any(rec_dir.iterdir()):
            kwargs_v3["text_recognition_model_dir"] = str(rec_dir)
        if cls_dir.is_dir() and any(cls_dir.iterdir()):
            kwargs_v3["textline_orientation_model_dir"] = str(cls_dir)

        try:
            self.ocr = PaddleOCR(**kwargs_v3)
            self._api_version = 3
        except (TypeError, ValueError) as init_exc:
            logger.warning(
                f"PaddleOCR 3.x init failed ({init_exc}); trying 2.x API"
            )
            kwargs_v2: dict[str, Any] = {
                "use_angle_cls": True,
                "lang": lang,
                "use_gpu": use_gpu,
                "show_log": False,
                "enable_mkldnn": False,
            }
            if det_dir.is_dir() and any(det_dir.iterdir()):
                kwargs_v2["det_model_dir"] = str(det_dir)
            if rec_dir.is_dir() and any(rec_dir.iterdir()):
                kwargs_v2["rec_model_dir"] = str(rec_dir)
            if cls_dir.is_dir() and any(cls_dir.iterdir()):
                kwargs_v2["cls_model_dir"] = str(cls_dir)
            self.ocr = PaddleOCR(**kwargs_v2)
            self._api_version = 2

        logger.info(
            f"PaddleOCR initialized (lang={lang}, device={device}, "
            f"api=v{self._api_version}, mkldnn=off)"
        )

    def _parse_v3_result(self, result: object, height: int) -> list[OCRBlock]:
        """Parse PaddleOCR 3.x / PaddleX OCRResult objects."""
        blocks: list[OCRBlock] = []
        pages = result if isinstance(result, list) else [result]

        for page in pages:
            if page is None:
                continue

            # OCRResult behaves like a dict
            if hasattr(page, "keys") or isinstance(page, dict):
                texts = page.get("rec_texts") if hasattr(page, "get") else None
                scores = page.get("rec_scores") if hasattr(page, "get") else None
                polys = page.get("rec_polys") if hasattr(page, "get") else None
                if polys is None and hasattr(page, "get"):
                    polys = page.get("dt_polys")
            else:
                continue

            if not texts:
                continue
            if scores is None:
                scores = [1.0] * len(texts)
            if polys is None:
                polys = [None] * len(texts)

            for text, confidence, poly in zip(texts, scores, polys):
                conf = float(confidence) if confidence is not None else 0.0
                if conf < config.OCR_MIN_CONFIDENCE:
                    continue
                if poly is None:
                    continue
                pts = np.array(poly)
                xs = pts[:, 0].tolist()
                ys = pts[:, 1].tolist()
                x_min, x_max = int(min(xs)), int(max(xs))
                y_min, y_max = int(min(ys)), int(max(ys))
                center_y = ((y_min + y_max) / 2.0) / max(height, 1)
                blocks.append(
                    OCRBlock(
                        text=str(text),
                        confidence=conf,
                        bbox=(x_min, y_min, x_max, y_max),
                        center_y=float(center_y),
                    )
                )
        return blocks

    def _parse_v2_result(self, result: object, height: int) -> list[OCRBlock]:
        """Parse classic PaddleOCR 2.x [[[pts], (text, conf)], ...] format."""
        blocks: list[OCRBlock] = []
        if not result or result[0] is None:
            return blocks

        for item in result[0]:
            if item is None or len(item) < 2:
                continue
            box_pts, (text, confidence) = item[0], item[1]
            if confidence < config.OCR_MIN_CONFIDENCE:
                continue
            xs = [int(p[0]) for p in box_pts]
            ys = [int(p[1]) for p in box_pts]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            center_y = ((y_min + y_max) / 2.0) / max(height, 1)
            blocks.append(
                OCRBlock(
                    text=str(text),
                    confidence=float(confidence),
                    bbox=(x_min, y_min, x_max, y_max),
                    center_y=float(center_y),
                )
            )
        return blocks

    def run(self, image: np.ndarray) -> list[OCRBlock]:
        """
        Run OCR on image.
        Parse PaddleOCR output into list[OCRBlock].
        Filter confidence < OCR_MIN_CONFIDENCE.
        Sort by center_y ascending (top to bottom).
        """
        if self.ocr is None:
            logger.warning("OCR engine not initialized — returning empty blocks")
            return []

        try:
            # Ensure 3-channel RGB for PaddleOCR
            if len(image.shape) == 2:
                img = np.stack([image] * 3, axis=-1)
            else:
                img = image

            height = img.shape[0]

            # PaddleX OCR pipelines typically expect BGR (OpenCV convention)
            if len(img.shape) == 3 and img.shape[2] == 3:
                img_bgr = img[:, :, ::-1].copy()
            else:
                img_bgr = img

            if self._api_version >= 3:
                # Prefer predict(); ocr() is deprecated alias in 3.x
                if hasattr(self.ocr, "predict"):
                    result = self.ocr.predict(img_bgr)
                else:
                    result = self.ocr.ocr(img_bgr)
                blocks = self._parse_v3_result(result, height)
                # If v3 parse got nothing but result looks like v2, try v2
                if not blocks and isinstance(result, list) and result and isinstance(result[0], list):
                    blocks = self._parse_v2_result(result, height)
            else:
                result = self.ocr.ocr(img_bgr, cls=True)
                blocks = self._parse_v2_result(result, height)

            blocks.sort(key=lambda b: b.center_y)
            return blocks

        except Exception as exc:
            logger.warning(f"OCR failed: {exc}")
            return []

    def extract_zone(
        self,
        blocks: list[OCRBlock],
        zone_top: float = config.HEADER_ZONE_TOP,
        zone_bottom: float = config.HEADER_ZONE_BOTTOM,
    ) -> list[OCRBlock]:
        """
        Filter OCRBlocks to only those whose center_y falls within
        [zone_top, zone_bottom]. Used to isolate the header zone.
        """
        return [b for b in blocks if zone_top <= b.center_y <= zone_bottom]

    def blocks_to_text(self, blocks: list[OCRBlock]) -> str:
        """Join texts from blocks (sorted by center_y) with newlines."""
        sorted_blocks = sorted(blocks, key=lambda b: b.center_y)
        return "\n".join(b.text for b in sorted_blocks)

    def run_dual(
        self,
        image: np.ndarray,
        handwriting_ocr: "HandwritingOCR | None" = None,
        handwriting_detector: "HandwritingDetector | None" = None,
    ) -> tuple[list[OCRBlock], list]:
        """
        Dual OCR strategy cho trang có cả chữ in lẫn viết tay.

        Nếu handwriting_ocr hoặc handwriting_detector là None:
            → Fallback về self.run(image), return (blocks, [])
        """
        if handwriting_ocr is None or handwriting_detector is None:
            return self.run(image), []

        if not handwriting_ocr.is_available():
            return self.run(image), []

        try:
            hw_regions = handwriting_detector.detect_handwritten_regions(image)
            if not hw_regions:
                return self.run(image), []

            printed_image, hw_crops = handwriting_detector.split_image(image, hw_regions)
            printed_blocks = self.run(printed_image)
            hw_raw = handwriting_ocr.recognize(hw_crops)
            logger.debug(
                f"Dual OCR: {len(printed_blocks)} printed + {len(hw_raw)} handwritten blocks "
                f"({len(hw_regions)} regions)"
            )
            return printed_blocks, hw_raw
        except Exception as exc:
            logger.warning(f"run_dual failed, falling back to printed-only OCR: {exc}")
            return self.run(image), []


# Quick test
if __name__ == "__main__":
    import sys

    import cv2

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.ocr_engine <image.png|path/to.pdf>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src}")
        sys.exit(1)

    if src.suffix.lower() == ".pdf":
        import fitz

        doc = fitz.open(str(src))
        page = doc.load_page(0)
        zoom = 200 / 72.0
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

    engine = OCREngine()
    blocks = engine.run(image)
    print(f"Found {len(blocks)} OCR blocks:")
    for b in blocks[:20]:
        print(f"  conf={b.confidence:.2f} y={b.center_y:.3f} | {b.text!r}")
    header = engine.extract_zone(blocks)
    print(f"\nHeader zone ({len(header)} blocks):\n{engine.blocks_to_text(header)}")
