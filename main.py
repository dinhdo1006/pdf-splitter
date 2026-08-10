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
        "--use-ocr-cache",
        action="store_true",
        help="Dùng cache OCR trong output/_ocr_cache (bỏ qua OCR nếu có)",
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
    device = parser.add_mutually_exclusive_group()
    device.add_argument(
        "--gpu",
        action="store_true",
        help="Ép OCR dùng GPU (init fail → vẫn fallback CPU)",
    )
    device.add_argument(
        "--cpu",
        action="store_true",
        help="Ép OCR dùng CPU",
    )
    return parser.parse_args()


def resolve_docs_dir(
    args: argparse.Namespace,
    output_dir: Path,
    identity=None,
) -> Path:
    """
    Dựng thư mục đảng viên Phụ lục 2 khi đủ họ tên + CCCD.

    Ưu tiên: CLI > OCR identity > bỏ qua (flat output).
    M1–M5: CLI > OCR > IDENTITY_DEFAULT_M_CODES.
    """
    from pipeline.identity_extractor import MemberIdentity, apply_cli_overrides
    from pipeline.party_path_builder import PartyPathBuilder

    ocr_ident = identity or MemberIdentity()
    merged = apply_cli_overrides(
        ocr_ident,
        ho_ten=args.ho_ten,
        cccd=args.cccd,
        m1=args.m1,
        m2=args.m2,
        m3=args.m3,
        m4=args.m4,
        m5=args.m5,
    )

    if not merged.has_member_folder_keys:
        if any(
            x is not None
            for x in (args.m1, args.m2, args.m3, args.m4, args.m5, args.cccd, args.ho_ten)
        ):
            logger.warning(
                "Thiếu họ tên hoặc CCCD (CLI/OCR) — xuất flat. "
                "Cần đủ để tạo cây thư mục Phụ lục 2."
            )
        elif not getattr(config, "IDENTITY_AUTO_PATH", True):
            logger.info("IDENTITY_AUTO_PATH=False — xuất flat.")
        return output_dir

    defaults = getattr(config, "IDENTITY_DEFAULT_M_CODES", ("0", "0", "0", "0", "0"))
    m1 = merged.m1 if merged.m1 is not None else defaults[0]
    m2 = merged.m2 if merged.m2 is not None else defaults[1]
    m3 = merged.m3 if merged.m3 is not None else defaults[2]
    m4 = merged.m4 if merged.m4 is not None else defaults[3]
    m5 = merged.m5 if merged.m5 is not None else defaults[4]

    used_default_m = all(
        getattr(merged, f"m{i}") is None for i in range(1, 6)
    ) and not any(
        getattr(args, f"m{i}") is not None for i in range(1, 6)
    )
    if used_default_m:
        logger.warning(
            f"Chưa có mã cấp ủy M1–M5 — dùng mặc định "
            f"{m1}.{m2}.{m3}.{m4}.{m5}. Nên truyền --m1..m5 khi biết."
        )

    builder = PartyPathBuilder(
        base_output_dir=output_dir,
        m1=m1,
        m2=m2,
        m3=m3,
        m4=m4,
        m5=m5,
        so_cccd=merged.folder_id,
        ho_ten_dang_vien=merged.ho_ten,
    )
    member_dir = builder.ensure_dirs()
    id_kind = "cccd" if merged.cccd else "tdv_fallback"
    logger.info(
        f"Phụ lục 2 member dir: {member_dir} "
        f"(ho_ten={merged.ho_ten!r}, {id_kind}={merged.folder_id!r}, "
        f"conf={merged.confidence:.2f})"
    )
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
    # docs_dir quyết định sau OCR (identity từ scan + CLI override)
    docs_dir = output_dir
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

    from pipeline.ocr_cache import cache_coverage, cache_dir_for, load_signal, save_signal

    ocr_cache_dir = cache_dir_for(output_dir, args.dpi, preprocess_enabled)
    use_cache = bool(args.use_ocr_cache)
    cache_full = False
    if use_cache:
        cov = cache_coverage(ocr_cache_dir, process_pages)
        cache_full = cov >= process_pages
        logger.info(
            f"OCR cache: {ocr_cache_dir} coverage={cov}/{process_pages}"
            + (" (skip OCR init)" if cache_full else "")
        )

    ocr = None
    if not cache_full:
        try:
            if getattr(args, "cpu", False):
                ocr_device: bool | str = False
            elif getattr(args, "gpu", False):
                ocr_device = True
            else:
                ocr_device = getattr(config, "OCR_USE_GPU", "auto")
            ocr = OCREngine(lang=config.OCR_LANG, use_gpu=ocr_device)
        except Exception as exc:
            logger.error(f"Failed to initialize OCR: {exc}")
            ingestor.close()
            return 1
    else:
        logger.info("Full OCR cache hit — boundary-only replay mode")

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
                signal = None
                if use_cache:
                    signal = load_signal(ocr_cache_dir, page_num)
                    if signal is not None:
                        logger.debug(f"Page {page_num}: loaded from OCR cache")

                if signal is None:
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
                    try:
                        save_signal(ocr_cache_dir, signal)
                    except Exception as cex:
                        logger.debug(f"OCR cache save skip page {page_num}: {cex}")

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
    blank_pages = list(getattr(detector, "blank_pages", []) or [])
    logger.info(
        f"Detected {len(groups)} documents, {len(orphan_pages)} orphans, "
        f"{len(blank_pages)} blanks across {process_pages} pages (before Pass-2)"
    )

    # Scrub TOC pages that may have been swallowed into groups
    try:
        from pipeline.page_audit import scrub_toc_from_groups

        groups, orphan_pages, scrubbed = scrub_toc_from_groups(
            groups, all_signals, orphan_pages
        )
        if scrubbed:
            logger.info(f"Scrubbed {len(scrubbed)} TOC pages from groups → orphans")
    except Exception as sex:
        logger.error(f"TOC scrub failed: {sex}")

    # Pass 2 — orphan reattachment (attach_prev only; TOC never reattach)
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

    # Pass-2b: orphan có catalog/header rõ → group mới (không nhồi vào prev)
    try:
        from pipeline.orphan_reattacher import promote_orphans_to_groups

        groups, orphan_pages, n_promoted = promote_orphans_to_groups(
            groups, orphan_pages, all_signals
        )
        if n_promoted:
            logger.info(
                f"Promoted {n_promoted} orphan pages → new catalog groups "
                f"({len(orphan_pages)} orphans left)"
            )
    except Exception as pex:
        logger.error(f"Promote orphans failed: {pex}")

    # Pass-2c: hút orphan sau form đã chạm soft-max (mid-page phiếu…)
    try:
        from pipeline.orphan_reattacher import absorb_trailing_orphans_after_capped_forms

        groups, orphan_pages, n_abs = absorb_trailing_orphans_after_capped_forms(
            groups, orphan_pages, all_signals
        )
        if n_abs:
            logger.info(
                f"Absorbed {n_abs} trailing orphan pages after capped forms "
                f"({len(orphan_pages)} orphans left)"
            )
    except Exception as aex:
        logger.error(f"Absorb trailing orphans failed: {aex}")

    # Post-classify nhóm CHUA_XAC_DINH → catalog nếu match được
    try:
        from pipeline.party_doc_matcher import refine_unknown_group_types

        n_refined = refine_unknown_group_types(groups, all_signals)
        if n_refined:
            logger.info(f"Refined {n_refined} CHUA_XAC_DINH groups → catalog")
    except Exception as rex:
        logger.error(f"Refine unknown types failed: {rex}")

    # Audit đủ trang trước export
    page_audit_report: dict = {}
    try:
        from pipeline.page_audit import audit_page_coverage

        page_audit_report = audit_page_coverage(
            process_pages, groups, orphan_pages, blank_pages
        )
        # Trang missing → đẩy vào orphan để không mất
        for pn in page_audit_report.get("missing_pages") or []:
            if pn not in orphan_pages:
                orphan_pages.append(pn)
                logger.warning(f"[audit] Recover missing page {pn} → orphan")
        orphan_pages = sorted(set(orphan_pages))
        if page_audit_report.get("missing_pages"):
            page_audit_report = audit_page_coverage(
                process_pages, groups, orphan_pages, blank_pages
            )
    except Exception as aex:
        logger.error(f"Page audit failed: {aex}")

    from pipeline.year_aware_sequencer import YearAwareSequencer, DocRecord

    def _group_ocr_blob(g) -> str:
        chunks: list[str] = []
        for pn in g.page_numbers[:4]:
            sig = all_signals.get(pn)
            if sig is None:
                continue
            chunks.append(getattr(sig, "header_text", "") or "")
            chunks.append((getattr(sig, "full_text", "") or "")[:800])
        return "\n".join(chunks)

    year_sequencer = YearAwareSequencer()
    doc_records = [
        DocRecord(
            doc_type_key=g.doc_type,
            raw_title=g.raw_title,
            page_numbers=list(g.page_numbers),
            pdf_order=i,
            doc_year=g.doc_year,
            ocr_blob=_group_ocr_blob(g),
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

    # Identity từ OCR → cây thư mục Phụ lục 2 (CLI override)
    member_identity = None
    try:
        from pipeline.identity_extractor import extract_member_identity_from_signals

        ocr_ident = extract_member_identity_from_signals(all_signals)
        # Bổ sung họ tên từ mục lục nếu OCR phiếu thiếu
        if (
            not ocr_ident.ho_ten
            and hoso_manifest is not None
            and getattr(hoso_manifest, "party_member_name", None)
        ):
            from pipeline.identity_extractor import _clean_person_name

            cleaned = _clean_person_name(hoso_manifest.party_member_name)
            ocr_ident.ho_ten = cleaned or hoso_manifest.party_member_name
            ocr_ident.sources.append("ho_ten:hoso_manifest")
            ocr_ident.confidence = max(ocr_ident.confidence, 0.5)
        docs_dir = resolve_docs_dir(args, output_dir, ocr_ident)
        from pipeline.identity_extractor import apply_cli_overrides

        member_identity = apply_cli_overrides(
            ocr_ident,
            ho_ten=args.ho_ten,
            cccd=args.cccd,
            m1=args.m1,
            m2=args.m2,
            m3=args.m3,
            m4=args.m4,
            m5=args.m5,
        )
    except Exception as iex:
        logger.error(f"Identity extract / path resolve failed: {iex}")
        docs_dir = output_dir

    if not groups and not orphan_pages:
        logger.warning("Nothing to export")
        exporter.close()
        ingestor.close()
        return 0

    export_result = exporter.export_all(
        groups,
        orphan_pages=orphan_pages,
        docs_dir=docs_dir,
        page_signals=all_signals,
    )

    # Manifest Phụ lục 2 trong member_dir (khi docs_dir ≠ flat output)
    try:
        if docs_dir.resolve() != output_dir.resolve():
            from pipeline.phu_luc2_manifest import write_member_manifest

            write_member_manifest(
                docs_dir,
                export_result=export_result,
                member_identity=(
                    member_identity.to_dict() if member_identity is not None else None
                ),
                source_pdf=str(input_path),
            )
    except Exception as mex:
        logger.error(f"write manifest_ho_so.json failed: {mex}")

    # Manifest extras: reattach + validation
    manifest_extra: dict = {}
    if member_identity is not None:
        try:
            manifest_extra["member_identity"] = member_identity.to_dict()
        except Exception:
            pass
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
    pages_exported = sum(
        r.get("page_count", 0)
        for r in (
            export_result.get("success", [])
            + export_result.get("tentative", [])
            + export_result.get("review", [])
        )
    ) + len(export_result.get("orphans", []))
    manifest_extra["quality"] = {
        "pages_processed": process_pages,
        "pages_exported": pages_exported,
        "pages_blank": len(blank_pages),
        "orphan_rate_pct": orphan_rate,
        "catalog_doc_count": catalog_docs,
        "total_doc_groups": total_docs,
    }
    if page_audit_report:
        manifest_extra["page_audit"] = page_audit_report

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
