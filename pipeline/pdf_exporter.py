"""Module 7: Export DocumentGroups + orphan pages (Phụ lục 2 naming)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import fitz
from loguru import logger

import config
from pipeline.boundary_detector import DocumentGroup
from pipeline.party_catalog import PARTY_DOC_CATALOG
from pipeline.party_filename_resolver import build_filename
from pipeline.review_namer import orphan_review_filename


class PDFExporter:
    """Extract page ranges from source PDF and write individual output files."""

    def __init__(self, source_pdf_path: str, output_dir: str) -> None:
        self.source_pdf_path = str(source_pdf_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir = self.output_dir / "_review"
        self.orphans_dir = self.review_dir / "orphans"
        self.tentative_dir = self.review_dir / "tentative"
        try:
            self.src_doc = fitz.open(self.source_pdf_path)
            logger.info(f"Exporter opened source: {self.source_pdf_path}")
        except Exception as exc:
            logger.error(f"Failed to open source PDF for export: {exc}")
            raise

    def _resolve_unique_path(self, directory: Path, filename: str) -> Path:
        """
        filename may already include .pdf.
        If collision: stem_v2.pdf, stem_v3.pdf...
        """
        directory.mkdir(parents=True, exist_ok=True)
        name = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"
        base = directory / name
        if not base.exists():
            return base

        stem = base.stem
        suffix = base.suffix
        version = 2
        while True:
            candidate = directory / f"{stem}_v{version}{suffix}"
            if not candidate.exists():
                return candidate
            version += 1

    def export_group(
        self,
        group: DocumentGroup,
        filename: str,
        dest_dir: Path | None = None,
    ) -> str:
        if not group.page_numbers:
            raise ValueError(f"Group {group.group_id} has no pages")

        target_dir = dest_dir or self.output_dir
        output_path = self._resolve_unique_path(target_dir, filename)
        out_doc = fitz.open()
        try:
            for page_num in group.page_numbers:
                out_doc.insert_pdf(
                    self.src_doc,
                    from_page=page_num - 1,
                    to_page=page_num - 1,
                )
            out_doc.save(
                str(output_path),
                garbage=config.PDF_COMPRESS_LEVEL,
                deflate=True,
            )
            logger.info(
                f"Exported {output_path.name} ({len(group.page_numbers)} pages)"
            )
            return str(output_path.resolve())
        except Exception as exc:
            logger.error(
                f"Failed to export group {group.group_id} ({filename}): {exc}"
            )
            raise
        finally:
            out_doc.close()

    def export_orphan_page(
        self,
        page_num: int,
        filename: str | None = None,
        dest_dir: Path | None = None,
    ) -> str | None:
        """Xuất 1 trang mồ côi → _review/orphans/<label>_page_XXXX.pdf"""
        if page_num < 1 or page_num > len(self.src_doc):
            logger.error(f"Orphan page {page_num} out of range")
            return None

        name = filename or f"ORPHAN_page_{page_num:04d}.pdf"
        orphan_dir = dest_dir or self.orphans_dir
        orphan_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._resolve_unique_path(orphan_dir, name)
        out_doc = fitz.open()
        try:
            out_doc.insert_pdf(
                self.src_doc,
                from_page=page_num - 1,
                to_page=page_num - 1,
            )
            out_doc.save(
                str(output_path),
                garbage=config.PDF_COMPRESS_LEVEL,
                deflate=True,
            )
            logger.warning(
                f"ORPHAN isolated: page {page_num} → {output_path}"
            )
            return str(output_path.resolve())
        except Exception as exc:
            logger.error(f"Failed to export orphan page {page_num}: {exc}")
            return None
        finally:
            out_doc.close()

    def _filename_for_group(
        self,
        group: DocumentGroup,
        type_totals: Counter,
    ) -> tuple[str, str]:
        """
        Returns (filename_with_pdf, bucket)
        bucket: 'success' | 'review' | 'tentative'
        """
        key = (group.doc_type or "").upper()
        is_tentative = bool(getattr(group, "_is_tentative", False))

        if key not in PARTY_DOC_CATALOG:
            safe = f"KHAC_group_{group.group_id:03d}.pdf"
            return safe, "tentative" if is_tentative else "review"

        total = type_totals[key]
        seq = getattr(group, "_sequence_number", 0) or 0
        if total <= 1:
            occurrence = 1
            total_for_name = 1
        else:
            occurrence = seq if seq >= 1 else 1
            total_for_name = total

        try:
            filename = build_filename(key, occurrence, total_for_name)
        except (KeyError, ValueError) as exc:
            logger.error(f"build_filename failed for {key}: {exc}")
            filename = f"KHAC_group_{group.group_id:03d}.pdf"
            return filename, "tentative" if is_tentative else "review"

        if is_tentative:
            return filename, "tentative"
        return filename, "success"

    def export_all(
        self,
        groups: list[DocumentGroup],
        orphan_pages: list[int] | None = None,
        docs_dir: Path | None = None,
        page_signals: dict | None = None,
    ) -> dict:
        """
        Export catalog groups + orphans + tentative.

        Returns:
            {
              "success": [...],
              "review": [...],
              "tentative": [...],
              "orphans": [...],
            }
        """
        orphan_pages = orphan_pages or []
        signals = page_signals or {}
        dest = docs_dir or self.output_dir
        dest.mkdir(parents=True, exist_ok=True)
        # _review nằm cạnh file success trong member_dir (Phụ lục 2)
        review_root = dest / "_review"
        review_misc = review_root / "khac"
        orphans_dir = review_root / "orphans"
        tentative_dir = review_root / "tentative"
        review_misc.mkdir(parents=True, exist_ok=True)
        orphans_dir.mkdir(parents=True, exist_ok=True)
        tentative_dir.mkdir(parents=True, exist_ok=True)

        catalog_keys = [
            (g.doc_type or "").upper()
            for g in groups
            if (g.doc_type or "").upper() in PARTY_DOC_CATALOG
        ]
        type_totals: Counter = Counter(catalog_keys)

        success: list[dict] = []
        review: list[dict] = []
        tentative: list[dict] = []
        orphans: list[dict] = []

        for group in groups:
            try:
                filename, bucket = self._filename_for_group(group, type_totals)
                if bucket == "success":
                    target = dest
                elif bucket == "tentative":
                    target = tentative_dir
                else:
                    target = review_misc
                output_path = self.export_group(group, filename, dest_dir=target)
                pages = group.page_numbers
                entry = {
                    "group_id": group.group_id,
                    "filename": Path(output_path).name,
                    "output_path": output_path,
                    "page_count": len(pages),
                    "page_range": [min(pages), max(pages)],
                    "raw_title": group.raw_title,
                    "doc_type": group.doc_type,
                    "doc_year": group.doc_year,
                    "sequence_number": getattr(group, "_sequence_number", 0),
                    "page_size_group": getattr(group, "page_size_group", "OTHER"),
                    "reattach_confidence": getattr(
                        group, "_reattach_confidence", 1.0
                    ),
                    "bucket": bucket,
                }
                if bucket == "success":
                    success.append(entry)
                elif bucket == "tentative":
                    tentative.append(entry)
                else:
                    review.append(entry)
            except Exception as exc:
                logger.error(
                    f"Skipping group {group.group_id} due to export error: {exc}"
                )
                continue

        for page_num in orphan_pages:
            label = orphan_review_filename(page_num, signals.get(page_num))
            path = self.export_orphan_page(
                page_num, filename=label, dest_dir=orphans_dir
            )
            if path:
                orphans.append(
                    {
                        "page_num": page_num,
                        "filename": Path(path).name,
                        "output_path": path,
                        "bucket": "orphan",
                        "review_label": Path(path).stem.rsplit("_page_", 1)[0]
                        if "_page_" in Path(path).stem
                        else "ORPHAN",
                    }
                )

        return {
            "success": success,
            "review": review,
            "tentative": tentative,
            "orphans": orphans,
        }

    def write_manifest(
        self,
        export_result: dict,
        manifest_path: str,
        extra: dict | None = None,
    ) -> None:
        success = export_result.get("success", [])
        review = export_result.get("review", [])
        tentative = export_result.get("tentative", [])
        orphans = export_result.get("orphans", [])
        manifest = {
            "total_success": len(success),
            "total_review_khac": len(review),
            "total_tentative": len(tentative),
            "total_orphans": len(orphans),
            "source_pdf": self.source_pdf_path,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "success_documents": success,
            "review_documents": review,
            "tentative_documents": tentative,
            "orphan_pages": orphans,
        }
        if extra:
            manifest.update(extra)
        path = Path(manifest_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            logger.info(f"Manifest written: {path}")
        except Exception as exc:
            logger.error(f"Failed to write manifest: {exc}")
            raise

    def close(self) -> None:
        if self.src_doc is not None:
            self.src_doc.close()
            self.src_doc = None  # type: ignore[assignment]
