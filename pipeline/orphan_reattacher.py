"""
pipeline/orphan_reattacher.py
=============================
Pass 2: sau state machine, xét lại orphan theo context trước/sau.

CHỈ attach_prev / attach_chain_prev — KHÔNG attach_next (tránh nuốt tài liệu).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from loguru import logger

import config
from pipeline.boundary_detector import DocumentGroup
from pipeline.continuation_validator import MULTI_PAGE_FORM_TYPES
from pipeline.signal_extractor import PageSignal

_STRONG_SIZE = getattr(
    config,
    "STRONG_SIZE_CONTINUATION_GROUPS",
    frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"}),
)


@dataclass
class ReattachDecision:
    orphan_page_num: int
    action: str  # "attach_prev" | "keep_orphan" | "attach_chain_prev"
    target_group_id: Optional[int]
    reason: str
    confidence: float


def reattach_orphans(
    groups: list[DocumentGroup],
    orphan_pages: list[int],
    all_signals: dict[int, PageSignal],
) -> tuple[list[DocumentGroup], list[int], list[ReattachDecision]]:
    """
    Returns (updated_groups, remaining_orphan_pages, decisions).
    """
    remaining_orphans = list(orphan_pages)
    updated_groups = list(groups)
    decisions: list[ReattachDecision] = []

    page_to_group: dict[int, DocumentGroup] = {}
    for g in updated_groups:
        for pn in g.page_numbers:
            page_to_group[pn] = g

    hard_min = getattr(config, "REATTACH_HARD_MIN_CONFIDENCE", 0.80)
    tent_min = getattr(config, "REATTACH_TENTATIVE_MIN_CONFIDENCE", 0.65)

    for orphan_pn in sorted(orphan_pages):
        if orphan_pn not in remaining_orphans:
            continue

        decision = _judge_orphan(
            orphan_pn, page_to_group, all_signals, remaining_orphans
        )
        decisions.append(decision)

        if decision.action not in ("attach_prev", "attach_chain_prev"):
            continue
        if decision.confidence < tent_min:
            decision.action = "keep_orphan"
            decision.reason = f"{decision.reason}|below_tentative_min"
            continue

        target_group = next(
            (g for g in updated_groups if g.group_id == decision.target_group_id),
            None,
        )
        if target_group is None:
            continue

        if orphan_pn not in target_group.page_numbers:
            target_group.page_numbers.append(orphan_pn)
            target_group.page_numbers.sort()
        if orphan_pn in remaining_orphans:
            remaining_orphans.remove(orphan_pn)
        page_to_group[orphan_pn] = target_group

        # Tentative nếu conf < hard threshold
        if decision.confidence < hard_min:
            target_group._is_tentative = True
            target_group._reattach_confidence = min(
                getattr(target_group, "_reattach_confidence", 1.0),
                decision.confidence,
            )
            logger.info(
                f"[reattach] TENTATIVE page {orphan_pn} → group "
                f"#{target_group.group_id} conf={decision.confidence:.2f} "
                f"({decision.reason})"
            )
        else:
            logger.info(
                f"[reattach] page {orphan_pn} → group #{target_group.group_id} "
                f"conf={decision.confidence:.2f} ({decision.reason})"
            )

    logger.info(
        f"[reattach] done: attached={len(orphan_pages) - len(remaining_orphans)}, "
        f"remaining_orphans={len(remaining_orphans)}"
    )
    return updated_groups, remaining_orphans, decisions


def _judge_orphan(
    orphan_pn: int,
    page_to_group: dict[int, DocumentGroup],
    all_signals: dict[int, PageSignal],
    current_orphans: list[int],
) -> ReattachDecision:
    signal = all_signals.get(orphan_pn)
    prev_pn = orphan_pn - 1
    next_pn = orphan_pn + 1
    prev_group = page_to_group.get(prev_pn)
    next_group = page_to_group.get(next_pn)

    # CASE 1: sandwiched between same group
    if (
        prev_group is not None
        and next_group is not None
        and prev_group.group_id == next_group.group_id
    ):
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="attach_prev",
            target_group_id=prev_group.group_id,
            reason="sandwiched_between_same_group",
            confidence=0.92,
        )

    # CASE 2: trailing page of multi-page form
    if (
        prev_group is not None
        and (prev_group.doc_type or "").upper() in MULTI_PAGE_FORM_TYPES
        and signal is not None
        and not signal.matched_doc_type
        and not getattr(signal, "is_toc", False)
        and float(getattr(signal, "boundary_score", 0.0) or 0.0) < 0.40
    ):
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="attach_prev",
            target_group_id=prev_group.group_id,
            reason="trailing_page_of_multi_page_form",
            confidence=0.75,
        )

    # CASE 3: orphan chain after multi-page form
    chain_start = orphan_pn
    orphan_set = set(current_orphans)
    while (chain_start - 1) in orphan_set:
        chain_start -= 1
    chain_prev_group = page_to_group.get(chain_start - 1)
    if (
        chain_prev_group is not None
        and (chain_prev_group.doc_type or "").upper() in MULTI_PAGE_FORM_TYPES
        and signal is not None
        and not signal.matched_doc_type
        and float(getattr(signal, "boundary_score", 0.0) or 0.0) < 0.35
    ):
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="attach_chain_prev",
            target_group_id=chain_prev_group.group_id,
            reason="orphan_chain_after_multi_page_form",
            confidence=0.70,
        )

    # CASE 4: same strong page-size group as prev
    if (
        prev_group is not None
        and signal is not None
        and signal.page_size_group in _STRONG_SIZE
        and prev_group.page_size_group == signal.page_size_group
        and not signal.matched_doc_type
        and not getattr(signal, "is_toc", False)
    ):
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="attach_prev",
            target_group_id=prev_group.group_id,
            reason="same_page_size_group_as_prev",
            confidence=0.80,
        )

    return ReattachDecision(
        orphan_page_num=orphan_pn,
        action="keep_orphan",
        target_group_id=None,
        reason="no_safe_reattach_found",
        confidence=1.0,
    )


def decisions_to_dicts(decisions: list[ReattachDecision]) -> list[dict]:
    return [asdict(d) for d in decisions]
