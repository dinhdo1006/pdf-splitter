"""Module 1: PDF page streaming — never load the entire PDF into memory."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import fitz
import numpy as np
from loguru import logger

import config


class PDFIngestor:
    """Stream pages from a large PDF one at a time to avoid RAM overflow."""

    def __init__(self, pdf_path: str, dpi: int = config.PDF_RENDER_DPI) -> None:
        """
        Open PDF with pymupdf (fitz).
        Store doc handle, total page count, dpi setting.
        Do NOT load all pages at once.
        """
        self.pdf_path = str(pdf_path)
        self.dpi = dpi
        self._doc = fitz.open(self.pdf_path)
        self._total_pages = len(self._doc)
        logger.info(f"Opened PDF: {self.pdf_path} ({self._total_pages} pages)")

    def _decide_dpi(self, width_pt: float, height_pt: float) -> int:
        """Adaptive DPI theo PAGE_SIZE_GROUPS; fallback self.dpi."""
        try:
            return config.page_size_ocr_dpi(width_pt, height_pt, default_dpi=self.dpi)
        except Exception:
            return self.dpi

    def stream_pages(
        self,
        max_pages: int | None = None,
        adaptive_dpi: bool = False,
    ) -> Generator[tuple[int, np.ndarray, float, float], None, None]:
        """
        Yield (page_number, image, width_pt, height_pt) for each page.
        page_number is 1-indexed.
        width_pt / height_pt lấy từ page.rect TRƯỚC khi render (không phụ thuộc DPI).
        adaptive_dpi=True → DPI theo size group; False → self.dpi cố định.
        """
        limit = self._total_pages if max_pages is None else min(max_pages, self._total_pages)

        for i in range(limit):
            page_num = i + 1
            page = self._doc.load_page(i)
            try:
                rect = page.rect
                width_pt = float(rect.width)
                height_pt = float(rect.height)
                dpi = self._decide_dpi(width_pt, height_pt) if adaptive_dpi else self.dpi
                zoom = dpi / 72.0
                matrix = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                # Ensure RGB (3 channels)
                if pix.n == 1:
                    img = np.stack([img.squeeze()] * 3, axis=-1)
                elif pix.n == 4:
                    img = img[:, :, :3]
                # Copy so pixmap can be freed
                img = img.copy()
                del pix
            finally:
                # Release page reference
                del page

            if page_num % config.LOG_PROGRESS_EVERY == 0:
                logger.info(f"Ingested {page_num}/{limit} pages")

            yield page_num, img, width_pt, height_pt

    @property
    def total_pages(self) -> int:
        """Return total page count."""
        return self._total_pages

    def extract_page_range(self, start: int, end: int, output_path: str) -> None:
        """
        Extract pages [start, end] (inclusive, 1-indexed) from the source PDF
        and save as a new PDF to output_path using pymupdf.
        Do NOT re-render — copy original PDF vectors/images to preserve quality.
        """
        out_doc = fitz.open()
        try:
            out_doc.insert_pdf(self._doc, from_page=start - 1, to_page=end - 1)
            out_doc.save(output_path, garbage=config.PDF_COMPRESS_LEVEL, deflate=True)
            logger.info(f"Extracted pages {start}–{end} → {output_path}")
        except Exception as exc:
            logger.error(f"Failed to extract pages {start}–{end}: {exc}")
            raise
        finally:
            out_doc.close()

    def close(self) -> None:
        """Close the source PDF file handle."""
        if self._doc is not None:
            self._doc.close()
            self._doc = None  # type: ignore[assignment]


# Quick test
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.ingestor <path/to.pdf>")
        sys.exit(1)

    pdf = sys.argv[1]
    if not Path(pdf).exists():
        print(f"File not found: {pdf}")
        sys.exit(1)

    ingestor = PDFIngestor(pdf, dpi=150)
    print(f"Total pages: {ingestor.total_pages}")
    for page_num, image, w_pt, h_pt in ingestor.stream_pages(max_pages=10):
        print(
            f"Page {page_num}: shape={image.shape}, dtype={image.dtype}, "
            f"size_pt={w_pt:.1f}x{h_pt:.1f}"
        )
        del image
    ingestor.close()
    print("Ingestor smoke test OK")
