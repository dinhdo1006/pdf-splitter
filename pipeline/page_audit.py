"""
pipeline/page_audit.py
======================
Audit đủ trang: mọi trang 1..N phải thuộc đúng một bucket
(group / orphan / blank). Báo missing / duplicate.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from pipeline.boundary_detector import DocumentGroup
from pipeline.signal_extractor import PageSignal


def scrub_toc_from_groups(
    groups: list[DocumentGroup],
    all_signals: dict[int, PageSignal],
    orphan_pages: list[int],
) -> tuple[list[DocumentGroup], list[int], list[int]]:
    """
    Gỡ trang TOC khỏi mọi DocumentGroup → đẩy sang orphan.
    Trả (groups, orphans, scrubbed_page_nums).
    """
    scrubbed: list[int] = []
    orphan_set = set(orphan_pages)
    kept_groups: list[DocumentGroup] = []

    for g in groups:
        stay: list[int] = []
        for pn in g.page_numbers:
            sig = all_signals.get(pn)
            if sig is not None and getattr(sig, "is_toc", False):
                scrubbed.append(pn)
                orphan_set.add(pn)
                logger.warning(
                    f"[audit] Scrub TOC page {pn} out of group #{g.group_id}"
                )
            else:
                stay.append(pn)
        if stay:
            g.page_numbers = stay
            kept_groups.append(g)
        else:
            logger.warning(
                f"[audit] Drop empty group #{g.group_id} after TOC scrub"
            )

    return kept_groups, sorted(orphan_set), scrubbed


def audit_page_coverage(
    pages_processed: int,
    groups: list[DocumentGroup],
    orphan_pages: list[int],
    blank_pages: list[int] | None = None,
) -> dict[str, Any]:
    """
    Kiểm tra mọi trang trong [1, pages_processed] được gán đúng 1 chỗ.
    """
    blank_pages = blank_pages or []
    owned: dict[int, str] = {}
    duplicates: list[dict[str, Any]] = []

    def _claim(pn: int, bucket: str) -> None:
        if pn in owned:
            duplicates.append(
                {"page": pn, "first": owned[pn], "second": bucket}
            )
        else:
            owned[pn] = bucket

    for g in groups:
        for pn in g.page_numbers:
            _claim(pn, f"group:{g.group_id}:{g.doc_type}")

    for pn in orphan_pages:
        _claim(pn, "orphan")

    for pn in blank_pages:
        _claim(pn, "blank")

    expected = set(range(1, pages_processed + 1))
    missing = sorted(expected - set(owned.keys()))
    extra = sorted(set(owned.keys()) - expected)

    report = {
        "pages_processed": pages_processed,
        "pages_in_groups": sum(len(g.page_numbers) for g in groups),
        "pages_orphan": len(orphan_pages),
        "pages_blank": len(blank_pages),
        "pages_accounted": len(owned),
        "missing_pages": missing,
        "extra_pages": extra,
        "duplicate_pages": duplicates,
        "complete": len(missing) == 0 and len(duplicates) == 0,
        "coverage_pct": round(
            100.0 * len(set(owned.keys()) & expected) / max(pages_processed, 1), 1
        ),
    }

    if missing:
        logger.warning(
            f"[audit] MISSING {len(missing)} pages: {missing[:30]}"
            + ("..." if len(missing) > 30 else "")
        )
    if duplicates:
        logger.warning(f"[audit] DUPLICATE pages: {duplicates[:10]}")
    if report["complete"]:
        logger.info(
            f"[audit] Page coverage OK: {report['coverage_pct']}% "
            f"(groups={report['pages_in_groups']}, "
            f"orphans={report['pages_orphan']}, blank={report['pages_blank']})"
        )
    else:
        logger.warning(
            f"[audit] Page coverage INCOMPLETE: {report['coverage_pct']}% "
            f"accounted={report['pages_accounted']}/{pages_processed}"
        )

    return report
