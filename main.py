"""
main.py — Vietnamese Party Member Hồ Sơ PDF Splitter (3-state anti-swallow)

Usage:
    python main.py -i large.pdf -o ./output
    python main.py -i large.pdf -o ./output --pages 40 --debug
    python main.py -i large.pdf -o ./output --m1 93 --m2 0 --m3 36 --m4 1 --m5 15 \\
        --cccd 012345678901 --ho-ten "Nguyen Van A"
    python main.py -i large.pdf -o ./output --adaptive-dpi
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger
from tqdm import tqdm

import config
from pipeline.boundary_detector import BoundaryDetector
from pipeline.continuation_validator import ContinuationValidator
from pipeline.ingestor import PDFIngestor
from pipeline.ocr_engine import OCREngine
from pipeline.pdf_exporter import PDFExporter
from pipeline.preprocessor import PagePreprocessor
from pipeline.signal_extractor import SignalExtractor


def setup_logging(debug: bool) -> Path:
    logger.remove()
    level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
    )

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = config.LOG_DIR / f"run_{timestamp}.log"
    logger.add(
        str(log_path),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function}:{line} | {message}",
        encoding="utf-8",
    )
    return log_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Party hồ sơ PDF splitter — 3-state anti-swallow "
            "(NEW / CONFIRMED_CONTINUATION / ORPHAN) + Pass-2 reattach"
        ),
    )
    parser.add_argument("--input", "-i", required=True, help="Path to input PDF")
    parser.add_argument(
        "--output",
        "-o",
        default=str(config.OUTPUT_DIR),
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=config.PDF_RENDER_DPI,
        help=f"Render DPI (default: {config.PDF_RENDER_DPI})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=config.BOUNDARY_THRESHOLD,
        help=f"Soft boundary score (default: {config.BOUNDARY_THRESHOLD})",
    )
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--no-preprocess",
        action="store_true",
        help="Skip deskew/CLAHE",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N pages (testing)",
    )
    parser.add_argument(
        "--adaptive-dpi",
        action="store_true",
        help="DPI theo page size group (BOOKLET→300, LANDSCAPE→250, A4→200)",
    )
    # Phase 2 — hierarchical path (Phụ lục 2.1)
    parser.add_argument("--m1", default=None, help="Mã cấp ủy M1 (2 chữ số)")
    parser.add_argument("--m2", default=None, help="Mã cấp ủy M2 (3 chữ số)")
    parser.add_argument("--m3", default=None, help="Mã cấp ủy M3 (3 chữ số)")
    parser.add_argument("--m4", default=None, help="Mã cấp ủy M4 (3 chữ số)")
    parser.add_argument("--m5", default=None, help="Mã cấp ủy M5 (3 chữ số)")
    parser.add_argument("--cccd", default=None, help="Số CCCD/CMND đảng viên")
    parser.add_argument(
        "--ho-ten",
        default=None,
        dest="ho_ten",
        help="Họ tên đảng viên (có dấu cũng được)",
    )
    parser.add_argument(
        "--enable-continuation-llm",
        action="store_true",
        help="Bật Rule 3 LLM trong ContinuationValidator (cần Ollama)",
    )
    return parser.parse_args()


def resolve_docs_dir(args: argparse.Namespace, output_dir: Path) -> Path:
    """
    Giai đoạn 1: flat output_dir.
    Giai đoạn 2: PartyPathBuilder khi đủ --m1..m5 --cccd --ho-ten.
    """
    keys = (args.m1, args.m2, args.m3, args.m4, args.m5, args.cccd, args.ho_ten)
    if all(k is None for k in keys):
        return output_dir

    if any(k is None for k in keys):
        logger.warning(
            "Thiếu một phần --m1..m5/--cccd/--ho-ten — xuất flat (Giai đoạn 1). "
            "Cần đủ cả 7 tham số cho cây thư mục Phụ lục 2."
        )
        return output_dir

    from pipeline.party_path_builder import PartyPathBuilder

    builder = PartyPathBuilder(
        base_output_dir=output_dir,
        m1=args.m1,
        m2=args.m2,
        m3=args.m3,
        m4=args.m4,
        m5=args.m5,
        so_cccd=args.cccd,
        ho_ten_dang_vien=args.ho_ten,
    )
    member_dir = builder.ensure_dirs()
    logger.info(f"Giai đoạn 2 — member dir: {member_dir}")
    return member_dir


def print_export_summary(export_result: dict) -> None:
    success = export_result.get("success", [])
    review = export_result.get("review", [])
    tentative = export_result.get("tentative", [])
    orphans = export_result.get("orphans", [])

    logger.info("")
    logger.info("========== SUCCESS EXPORTS (Phụ lục 1–2) ==========")
    if not success:
        logger.info("(không có file catalog)")
    else:
        for i, r in enumerate(success, 1):
            lo, hi = r["page_range"]
            logger.info(
                f"  {i:02d}. {r['filename']}  pages={r['page_count']}  "
                f"range={lo}-{hi}  type={r.get('doc_type')}"
            )

    if tentative:
        logger.info("")
        logger.info("========== TENTATIVE (reattach conf < 0.80) ==========")
        for i, r in enumerate(tentative, 1):
            lo, hi = r["page_range"]
            logger.info(
                f"  {i:02d}. {r['filename']}  pages={r['page_count']}  "
                f"range={lo}-{hi}  conf={r.get('reattach_confidence')}"
            )

    if review:
        logger.info("")
        logger.info("========== REVIEW / KHAC ==========")
        for i, r in enumerate(review, 1):
            lo, hi = r["page_range"]
            logger.info(
                f"  {i:02d}. {r['filename']}  pages={r['page_count']}  "
                f"range={lo}-{hi}"
            )

    logger.info("")
    logger.info("========== ISOLATED ORPHANS ==========")
    if not orphans:
        logger.info("(không có orphan)")
    else:
        for i, r in enumerate(orphans, 1):
            logger.info(
                f"  {i:02d}. page {r['page_num']:04d} → {r['filename']}"
            )
    logger.info("")


def main() -> int:
    args = parse_args()
    log_path = setup_logging(args.debug)

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input PDF not found: {input_path}")
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = resolve_docs_dir(args, output_dir)
    preprocess_enabled = not args.no_preprocess

    try:
        ingestor = PDFIngestor(str(input_path), dpi=args.dpi)
    except Exception as exc:
        logger.error(f"Failed to open PDF: {exc}")
        return 1

    total_pages = ingestor.total_pages
    process_pages = min(args.pages, total_pages) if args.pages else total_pages

    logger.info(
        f"Starting 3-state pipeline on {input_path} "
        f"({total_pages} pages, processing {process_pages})"
    )
    logger.info(f"Log file: {log_path}")
    logger.info(
        f"Settings: dpi={args.dpi}, adaptive_dpi={args.adaptive_dpi}, "
        f"threshold={args.threshold}, "
        f"high_threshold={config.HIGH_BOUNDARY_THRESHOLD}, "
        f"preprocess={preprocess_enabled}"
    )

    preprocessor = PagePreprocessor(
        max_skew_angle=config.MAX_SKEW_ANGLE,
        skew_threshold=config.SKEW_THRESHOLD,
    )

    try:
        ocr = OCREngine(lang=config.OCR_LANG, use_gpu=config.OCR_USE_GPU)
    except Exception as exc:
        logger.error(f"Failed to initialize OCR: {exc}")
        ingestor.close()
        return 1

    hw_detector = None
    hw_ocr = None
    if config.ENABLE_HANDWRITING_OCR:
        try:
            from pipeline.handwriting_detector import HandwritingDetector
            from pipeline.handwriting_ocr import HandwritingOCR

            hw_detector = HandwritingDetector(
                stroke_irregularity_threshold=config.HW_STROKE_THRESHOLD,
                min_region_area=config.HW_MIN_REGION_AREA,
            )
            hw_ocr = HandwritingOCR(
                use_gpu=config.TROCR_USE_GPU,
                cache_dir=config.TROCR_CACHE_DIR,
            )
            if not hw_ocr.is_available():
                logger.warning("TrOCR không khả dụng — bỏ qua handwriting OCR")
                hw_ocr = None
            else:
                logger.info("Handwriting OCR (TrOCR) đã sẵn sàng")
        except ImportError as e:
            logger.warning(f"Không load được handwriting module: {e}")
            hw_detector = None
            hw_ocr = None

    llm_referee = None
    use_cont_llm = args.enable_continuation_llm or getattr(
        config, "ENABLE_CONTINUATION_LLM", False
    )
    if use_cont_llm:
        try:
            from pipeline.llm_referee import LLMReferee

            llm_referee = LLMReferee()
            if not llm_referee.is_available:
                logger.warning("Ollama không sẵn sàng — Rule 3 LLM tắt")
                llm_referee = None
            else:
                logger.info("Continuation LLM (Rule 3) sẵn sàng")
        except ImportError as e:
            logger.warning(f"Không load LLMReferee: {e}")
            llm_referee = None

    validator = ContinuationValidator(
        enable_llm=bool(llm_referee),
    )
    extractor = SignalExtractor(ocr)
    detector = BoundaryDetector(
        boundary_threshold=args.threshold,
        low_confidence_range=config.LOW_CONFIDENCE_RANGE,
        high_threshold=getattr(config, "HIGH_BOUNDARY_THRESHOLD", 0.70),
        continuation_validator=validator,
        llm_referee=llm_referee,
    )
    exporter = PDFExporter(str(input_path), str(output_dir))

    all_signals: dict = {}
    hoso_manifest = None

    try:
        page_iter = ingestor.stream_pages(
            max_pages=process_pages,
            adaptive_dpi=bool(args.adaptive_dpi),
        )
        for page_num, raw_image, width_pt, height_pt in tqdm(
            page_iter,
            total=process_pages,
            desc="Processing pages",
            unit="page",
        ):
            try:
                if preprocess_enabled:
                    image, skew_angle = preprocessor.process(raw_image)
                    if abs(skew_angle) > config.SKEW_THRESHOLD:
                        logger.debug(
                            f"Page {page_num}: corrected skew {skew_angle:.1f}°"
                        )
                else:
                    image = raw_image

                signal = extractor.extract(
                    page_num,
                    image,
                    hw_ocr,
                    hw_detector,
                    width_pt=width_pt,
                    height_pt=height_pt,
                )
                decision = detector.process_page(signal)
                signal.boundary_score = decision.score
                all_signals[page_num] = signal

                # Manifest từ trang Mục Lục (lấy bản đầu đủ tin)
                if hoso_manifest is None and (
                    signal.is_toc or "muc luc" in (signal.header_text or "").lower()
                ):
                    try:
                        from pipeline.manifest_extractor import extract_manifest

                        m = extract_manifest(
                            page_num, signal.full_text, signal.header_text
                        )
                        if m and m.extraction_confidence >= 0.3:
                            hoso_manifest = m
                            logger.info(
                                f"Manifest found at page {page_num}: "
                                f"{len(m.entries)} entries, "
                                f"confidence={m.extraction_confidence:.2f}"
                            )
                    except Exception as mex:
                        logger.debug(f"Manifest extract skip page {page_num}: {mex}")

                logger.debug(
                    f"Page {page_num} | {decision.page_class.value} "
                    f"| score={decision.score:.2f} | size={signal.page_size_group} "
                    f"| conf={decision.confidence} | {decision.reasoning}"
                )
            except Exception as exc:
                logger.warning(
                    f"Page {page_num} processing error (skipped signals): {exc}"
                )
            finally:
                try:
                    del raw_image
                except NameError:
                    pass
                try:
                    del image
                except NameError:
                    pass

                if page_num % config.GC_EVERY_N_PAGES == 0:
                    gc.collect()

    except KeyboardInterrupt:
        logger.warning("Interrupted by user — finalizing partial results")
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
        exporter.close()
        ingestor.close()
        return 1

    groups, orphan_pages = detector.finalize()
    logger.info(
        f"Detected {len(groups)} documents, {len(orphan_pages)} orphans "
        f"across {process_pages} pages (before Pass-2)"
    )

    # Pass 2 — orphan reattachment (attach_prev only)
    reattach_decisions: list = []
    try:
        from pipeline.orphan_reattacher import decisions_to_dicts, reattach_orphans

        groups, orphan_pages, reattach_decisions = reattach_orphans(
            groups, orphan_pages, all_signals
        )
        logger.info(
            f"After Pass-2: {len(groups)} documents, "
            f"{len(orphan_pages)} orphans remaining"
        )
    except Exception as rex:
        logger.error(f"Pass-2 reattach failed (giữ orphans gốc): {rex}")

    from pipeline.year_aware_sequencer import YearAwareSequencer, DocRecord

    year_sequencer = YearAwareSequencer()
    doc_records = [
        DocRecord(
            doc_type_key=g.doc_type,
            raw_title=g.raw_title,
            page_numbers=list(g.page_numbers),
            pdf_order=i,
            doc_year=g.doc_year,
        )
        for i, g in enumerate(groups)
    ]

    try:
        doc_records = year_sequencer.assign_sequence(doc_records)
    except Exception as _seq_exc:
        logger.error(
            f"YearAwareSequencer lỗi: {_seq_exc} — giữ sequence_number=0"
        )

    for group, record in zip(groups, doc_records):
        group._sequence_number = record.sequence_number
        if group.doc_year is None and record.doc_year is not None:
            group.doc_year = record.doc_year

    try:
        summary = year_sequencer.build_year_summary(doc_records)
        logger.info(
            f"Year summary: {summary['with_year']}/{summary['total']} có năm, "
            f"{summary['without_year']} không có năm."
        )
    except Exception as _sum_exc:
        logger.error(f"build_year_summary() lỗi: {_sum_exc}")

    if not groups and not orphan_pages:
        logger.warning("Nothing to export")
        exporter.close()
        ingestor.close()
        return 0

    export_result = exporter.export_all(
        groups,
        orphan_pages=orphan_pages,
        docs_dir=docs_dir,
    )

    # Manifest extras: reattach + validation
    manifest_extra: dict = {}
    try:
        from pipeline.orphan_reattacher import decisions_to_dicts

        manifest_extra["reattach_decisions"] = decisions_to_dicts(reattach_decisions)
        attached = sum(
            1
            for d in reattach_decisions
            if d.action in ("attach_prev", "attach_chain_prev")
        )
        manifest_extra["reattach_summary"] = {
            "decisions": len(reattach_decisions),
            "attached": attached,
            "remaining_orphans": len(orphan_pages),
        }
    except Exception:
        pass

    if hoso_manifest is not None:
        try:
            from pipeline.manifest_extractor import (
                manifest_to_dict,
                validate_output_vs_manifest,
            )

            exported_types = [
                r.get("doc_type", "")
                for r in export_result.get("success", [])
                + export_result.get("tentative", [])
            ]
            validation = validate_output_vs_manifest(hoso_manifest, exported_types)
            manifest_extra["hoso_manifest"] = manifest_to_dict(hoso_manifest)
            manifest_extra["validation"] = validation
            if validation.get("missing_in_output"):
                logger.warning(
                    f"Missing doc types vs Mục Lục: "
                    f"{validation['missing_in_output']}"
                )
            logger.info(
                f"Manifest validation completeness="
                f"{validation.get('completeness_pct')}%"
            )
        except Exception as vex:
            logger.error(f"Manifest validation failed: {vex}")

    orphan_rate = (
        round(100.0 * len(orphan_pages) / max(process_pages, 1), 1)
        if process_pages
        else 0.0
    )
    catalog_docs = len(export_result.get("success", [])) + len(
        export_result.get("tentative", [])
    )
    total_docs = catalog_docs + len(export_result.get("review", []))
    manifest_extra["quality"] = {
        "pages_processed": process_pages,
        "orphan_rate_pct": orphan_rate,
        "catalog_doc_count": catalog_docs,
        "total_doc_groups": total_docs,
    }

    manifest_path = output_dir / "manifest.json"
    try:
        exporter.write_manifest(
            export_result, str(manifest_path), extra=manifest_extra or None
        )
    except Exception as exc:
        logger.error(f"Manifest write failed: {exc}")

    if detector.low_confidence_pages:
        review_path = config.LOG_DIR / "low_confidence_pages.json"
        try:
            with open(review_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "count": len(detector.low_confidence_pages),
                        "pages": detector.low_confidence_pages,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.warning(
                f"{len(detector.low_confidence_pages)} low-confidence pages → "
                f"{review_path}"
            )
        except Exception as exc:
            logger.error(f"Failed to write low-confidence file: {exc}")

    print_export_summary(export_result)

    exporter.close()
    ingestor.close()
    gc.collect()

    n_ok = len(export_result.get("success", []))
    n_or = len(export_result.get("orphans", []))
    n_tent = len(export_result.get("tentative", []))
    logger.info(
        f"Done. success={n_ok}, tentative={n_tent}, orphans={n_or}, "
        f"orphan_rate={orphan_rate}%, output={output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
