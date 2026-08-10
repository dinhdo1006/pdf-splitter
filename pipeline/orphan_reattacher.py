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
from pipeline.continuation_validator import MULTI_PAGE_FORM_TYPES, soft_max_pages_for
from pipeline.doc_identity import is_quyet_dinh_type, looks_like_standalone_minutes
from pipeline.signal_extractor import PageSignal

_STRONG_SIZE = getattr(
    config,
    "STRONG_SIZE_CONTINUATION_GROUPS",
    frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"}),
)


def _looks_like_standalone_minutes(signal: Optional[PageSignal]) -> bool:
    if signal is None:
        return False
    return looks_like_standalone_minutes(
        getattr(signal, "header_text", "") or "",
        getattr(signal, "full_text", "") or "",
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
    Chạy nhiều pass — orphan kẹp giữa hai orphan khác (vd. 22/23/24 phiếu)
    chỉ sandwich được sau khi hai đầu đã gắn.
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

    max_passes = 4
    for pass_i in range(1, max_passes + 1):
        if not remaining_orphans:
            break
        attached_this_pass = 0
        # Snapshot — chỉ xét orphan còn lại
        for orphan_pn in sorted(list(remaining_orphans)):
            decision = _judge_orphan(
                orphan_pn, page_to_group, all_signals, remaining_orphans
            )

            if decision.action not in ("attach_prev", "attach_chain_prev"):
                if pass_i == max_passes:
                    decisions.append(decision)
                continue
            if decision.confidence < tent_min:
                decision.action = "keep_orphan"
                decision.reason = f"{decision.reason}|below_tentative_min"
                if pass_i == max_passes:
                    decisions.append(decision)
                continue

            target_group = next(
                (g for g in updated_groups if g.group_id == decision.target_group_id),
                None,
            )
            if target_group is None:
                if pass_i == max_passes:
                    decisions.append(decision)
                continue

            # Soft-max cứng — mọi case (kể cả sandwich) không vượt cap
            max_soft = soft_max_pages_for(target_group.doc_type)
            if max_soft is not None and len(target_group.page_numbers) >= max_soft:
                decision.action = "keep_orphan"
                decision.reason = (
                    f"{decision.reason}|blocked_soft_max({max_soft})"
                )
                if pass_i == max_passes:
                    decisions.append(decision)
                continue

            if orphan_pn not in target_group.page_numbers:
                target_group.page_numbers.append(orphan_pn)
                target_group.page_numbers.sort()
            if orphan_pn in remaining_orphans:
                remaining_orphans.remove(orphan_pn)
            page_to_group[orphan_pn] = target_group
            attached_this_pass += 1
            decision.reason = f"{decision.reason}|pass{pass_i}"
            decisions.append(decision)

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

        if attached_this_pass == 0:
            # Ghi keep_orphan còn lại
            for orphan_pn in sorted(remaining_orphans):
                decisions.append(
                    _judge_orphan(
                        orphan_pn, page_to_group, all_signals, remaining_orphans
                    )
                )
            break

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

    # TOC / mục lục: không bao giờ reattach vào group tài liệu
    if signal is not None and getattr(signal, "is_toc", False):
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="keep_orphan",
            target_group_id=None,
            reason="toc_never_reattach",
            confidence=1.0,
        )

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
        # Quyết định gần như luôn 1 trang — không sandwich lấp lỗ (hay nuốt biên bản).
        if is_quyet_dinh_type(prev_group.doc_type):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="no_sandwich_into_quyet_dinh",
                confidence=1.0,
            )
        if _looks_like_standalone_minutes(signal):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="minutes_sandwich_keep_orphan",
                confidence=1.0,
            )
        max_soft = soft_max_pages_for(prev_group.doc_type)
        if max_soft is not None and len(prev_group.page_numbers) >= max_soft:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason=f"sandwich_blocked_soft_max({max_soft})",
                confidence=1.0,
            )
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
        and not is_quyet_dinh_type(prev_group.doc_type)
        and signal is not None
        and not signal.matched_doc_type
        and not getattr(signal, "is_toc", False)
        and (
            float(getattr(signal, "boundary_score", 0.0) or 0.0) < 0.40
            or (
                getattr(signal, "is_form_section", False)
                and (prev_group.doc_type or "").upper()
                in {
                    "PHIEU_DANG_VIEN",
                    "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                }
            )
        )
    ):
        from pipeline.doc_identity import (
            looks_like_kiem_diem_header,
            looks_like_phieu_bo_sung,
            looks_like_phieu_dang_vien,
            looks_like_quyet_dinh_or_nghi_quyet,
        )

        open_t = (prev_group.doc_type or "").upper()
        header = getattr(signal, "header_text", "") or ""
        full = getattr(signal, "full_text", "") or ""
        max_soft = soft_max_pages_for(open_t)
        if max_soft is not None and len(prev_group.page_numbers) >= max_soft:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason=f"prev_group_at_max_pages({max_soft})",
                confidence=1.0,
            )
        if looks_like_phieu_bo_sung(header, full) or looks_like_phieu_dang_vien(
            header, full
        ):
            if "PHIEU" not in open_t:
                return ReattachDecision(
                    orphan_page_num=orphan_pn,
                    action="keep_orphan",
                    target_group_id=None,
                    reason="phieu_not_into_non_phieu_trailing",
                    confidence=1.0,
                )
        if looks_like_kiem_diem_header(header, full) and not open_t.startswith(
            "BAN_TU_KIEM"
        ):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="kiem_diem_not_into_other_trailing",
                confidence=1.0,
            )
        if looks_like_quyet_dinh_or_nghi_quyet(header, full):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="quyet_dinh_trailing_keep_orphan",
                confidence=1.0,
            )
        if _looks_like_standalone_minutes(signal):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="minutes_trailing_keep_orphan",
                confidence=1.0,
            )
        open_sg = prev_group.page_size_group or "OTHER"
        curr_sg = getattr(signal, "page_size_group", "OTHER") or "OTHER"
        if open_sg in _STRONG_SIZE and curr_sg in {"A4_PORTRAIT", "A4_MEDIUM"}:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="size_mismatch_trailing_keep_orphan",
                confidence=1.0,
            )
        return ReattachDecision(
            orphan_page_num=orphan_pn,
            action="attach_prev",
            target_group_id=prev_group.group_id,
            reason="trailing_page_of_multi_page_form",
            confidence=(
                0.88
                if (prev_group.doc_type or "").upper()
                in {
                    "PHIEU_DANG_VIEN",
                    "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                }
                else 0.75
            ),
        )

    # CASE 3: orphan chain after multi-page form
    chain_start = orphan_pn
    orphan_set = set(current_orphans)
    while (chain_start - 1) in orphan_set:
        # Không nhảy qua TOC trong chuỗi orphan
        prev_sig = all_signals.get(chain_start - 1)
        if prev_sig is not None and getattr(prev_sig, "is_toc", False):
            break
        chain_start -= 1
    chain_prev_group = page_to_group.get(chain_start - 1)
    if (
        chain_prev_group is not None
        and (chain_prev_group.doc_type or "").upper() in MULTI_PAGE_FORM_TYPES
        and signal is not None
        and not signal.matched_doc_type
        and float(getattr(signal, "boundary_score", 0.0) or 0.0) < 0.35
    ):
        from pipeline.doc_identity import (
            looks_like_kiem_diem_header,
            looks_like_phieu_bo_sung,
            looks_like_phieu_dang_vien,
            looks_like_quyet_dinh_or_nghi_quyet,
        )

        chain_t = (chain_prev_group.doc_type or "").upper()
        max_soft = soft_max_pages_for(chain_t)
        if max_soft is not None and len(chain_prev_group.page_numbers) >= max_soft:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason=f"chain_prev_at_max_pages({max_soft})",
                confidence=1.0,
            )
        if _looks_like_standalone_minutes(signal):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="minutes_chain_keep_orphan",
                confidence=1.0,
            )
        # Không gắn loại form khác vào group đang mở (vd. phiếu ĐV → lý lịch)
        if looks_like_phieu_dang_vien(
            getattr(signal, "header_text", ""), getattr(signal, "full_text", "") or ""
        ) or looks_like_phieu_bo_sung(
            getattr(signal, "header_text", ""), getattr(signal, "full_text", "") or ""
        ):
            if "PHIEU" not in chain_t:
                return ReattachDecision(
                    orphan_page_num=orphan_pn,
                    action="keep_orphan",
                    target_group_id=None,
                    reason="phieu_not_into_non_phieu_chain",
                    confidence=1.0,
                )
        if looks_like_kiem_diem_header(
            getattr(signal, "header_text", ""), getattr(signal, "full_text", "") or ""
        ) and not chain_t.startswith("BAN_TU_KIEM"):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="kiem_diem_not_into_other_chain",
                confidence=1.0,
            )
        if looks_like_quyet_dinh_or_nghi_quyet(
            getattr(signal, "header_text", ""), getattr(signal, "full_text", "") or ""
        ):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="quyet_dinh_chain_keep_orphan",
                confidence=1.0,
            )
        # Không gắn A4 vào booklet/landscape lý lịch
        open_sg = chain_prev_group.page_size_group or "OTHER"
        curr_sg = getattr(signal, "page_size_group", "OTHER") or "OTHER"
        if open_sg in _STRONG_SIZE and curr_sg in {"A4_PORTRAIT", "A4_MEDIUM"}:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="size_mismatch_chain_keep_orphan",
                confidence=1.0,
            )
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
        and not is_quyet_dinh_type(prev_group.doc_type)
        and not _looks_like_standalone_minutes(signal)
    ):
        prev_t = (prev_group.doc_type or "").upper()
        # Không gắn tiếp vào phiếu xin ý kiến (1 trang)
        if prev_t.startswith("TONG_HOP_Y_KIEN"):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="no_reattach_into_y_kien",
                confidence=1.0,
            )
        from pipeline.doc_identity import looks_like_kiem_diem_header

        header = getattr(signal, "header_text", "") or ""
        full = getattr(signal, "full_text", "") or ""
        if looks_like_kiem_diem_header(header, full) and not prev_t.startswith(
            "BAN_TU_KIEM"
        ):
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason="kiem_diem_not_into_size_prev",
                confidence=1.0,
            )
        max_soft = soft_max_pages_for(prev_group.doc_type)
        if max_soft is not None and len(prev_group.page_numbers) >= max_soft:
            return ReattachDecision(
                orphan_page_num=orphan_pn,
                action="keep_orphan",
                target_group_id=None,
                reason=f"size_prev_at_max_pages({max_soft})",
                confidence=1.0,
            )
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


def absorb_trailing_orphans_after_capped_forms(
    groups: list[DocumentGroup],
    orphan_pages: list[int],
    all_signals: dict[int, PageSignal],
) -> tuple[list[DocumentGroup], list[int], int]:
    """
    Sau soft-max: orphan liền sau group form đã đầy cap → mở group mới cùng loại
    và hút chuỗi mid-page (vd. phiếu 144–149 cap → 150–170 thành phiếu mới).
    """
    if not orphan_pages or not groups:
        return groups, orphan_pages, 0

    orphan_set = set(orphan_pages)
    used: set[int] = set()
    new_groups: list[DocumentGroup] = []
    next_id = max((g.group_id for g in groups), default=0) + 1
    promoted = 0

    # Index group theo trang cuối
    end_page_to_group: dict[int, DocumentGroup] = {}
    for g in groups:
        if g.page_numbers:
            end_page_to_group[max(g.page_numbers)] = g

    for end_pn in sorted(end_page_to_group.keys()):
        g = end_page_to_group[end_pn]
        dtype = (g.doc_type or "").upper()
        if dtype not in MULTI_PAGE_FORM_TYPES and not dtype.startswith("BAN_TU_KIEM"):
            continue
        max_soft = soft_max_pages_for(dtype)
        if max_soft is None or len(g.page_numbers) < max_soft:
            continue
        start = end_pn + 1
        if start not in orphan_set or start in used:
            continue
        sig0 = all_signals.get(start)
        if sig0 is None or getattr(sig0, "is_toc", False):
            continue
        if _looks_like_standalone_minutes(sig0):
            continue
        # Không hút nếu orphan đã có loại khác rõ
        inferred = _infer_orphan_doc_type(sig0)
        if inferred and inferred != dtype:
            continue
        # Không bắt đầu chuỗi phiếu bằng header kiểm điểm / QĐ
        from pipeline.doc_identity import (
            looks_like_ke_khai_tai_san,
            looks_like_kiem_diem_header,
            looks_like_phieu_bo_sung,
            looks_like_phieu_dang_vien,
            looks_like_phieu_xin_y_kien,
            looks_like_quyet_dinh_or_nghi_quyet,
        )

        h0 = getattr(sig0, "header_text", "") or ""
        f0 = getattr(sig0, "full_text", "") or ""
        if dtype.startswith("PHIEU") and (
            looks_like_kiem_diem_header(h0, f0)
            or looks_like_quyet_dinh_or_nghi_quyet(h0, f0)
        ):
            continue
        if dtype.startswith("BAN_TU_KIEM") and (
            looks_like_phieu_bo_sung(h0, f0)
            or looks_like_ke_khai_tai_san(h0, f0)
            or looks_like_phieu_dang_vien(h0, f0)
            or looks_like_phieu_xin_y_kien(h0, f0)
        ):
            continue

        chain = [start]
        used.add(start)
        cur = start + 1
        while cur in orphan_set and cur not in used:
            sig_n = all_signals.get(cur)
            if sig_n is None or getattr(sig_n, "is_toc", False):
                break
            if _looks_like_standalone_minutes(sig_n):
                break
            hn = getattr(sig_n, "header_text", "") or ""
            fn = getattr(sig_n, "full_text", "") or ""
            if dtype.startswith("PHIEU") and (
                looks_like_kiem_diem_header(hn, fn)
                or looks_like_quyet_dinh_or_nghi_quyet(hn, fn)
            ):
                break
            if dtype.startswith("BAN_TU_KIEM") and (
                looks_like_phieu_bo_sung(hn, fn)
                or looks_like_ke_khai_tai_san(hn, fn)
                or looks_like_phieu_dang_vien(hn, fn)
                or looks_like_phieu_xin_y_kien(hn, fn)
            ):
                break
            inferred_n = _infer_orphan_doc_type(sig_n)
            if inferred_n and inferred_n != dtype:
                break
            if (getattr(sig_n, "text_density", 0) or 0) < 0.008:
                break
            chain.append(cur)
            used.add(cur)
            cur += 1
            if max_soft is not None and len(chain) >= max_soft:
                break

        ng = DocumentGroup(
            group_id=next_id,
            raw_title=(getattr(sig0, "header_text", "") or "")[:200],
            doc_type=dtype,
            page_numbers=list(chain),
            page_size_group=getattr(sig0, "page_size_group", g.page_size_group)
            or "OTHER",
        )
        next_id += 1
        new_groups.append(ng)
        promoted += len(chain)
        logger.info(
            f"[absorb] after capped {dtype} @{end_pn}: pages "
            f"{chain[0]}-{chain[-1]} → group #{ng.group_id}"
        )

    if not new_groups:
        return groups, orphan_pages, 0
    remaining = [p for p in orphan_pages if p not in used]
    return list(groups) + new_groups, remaining, promoted


def decisions_to_dicts(decisions: list[ReattachDecision]) -> list[dict]:
    return [asdict(d) for d in decisions]


def _infer_orphan_doc_type(signal: PageSignal) -> Optional[str]:
    """Suy loại catalog từ signal orphan (matcher + heuristic)."""
    from pipeline.doc_identity import (
        looks_like_kiem_diem_header,
        looks_like_ly_lich_header,
        looks_like_phieu_bo_sung,
        looks_like_phieu_dang_vien,
        looks_like_quyet_dinh_or_nghi_quyet,
    )
    from pipeline.party_doc_matcher import get_matcher
    from pipeline.party_catalog import PARTY_DOC_CATALOG

    dtype = (getattr(signal, "matched_doc_type", "") or "").upper()
    if dtype and dtype in PARTY_DOC_CATALOG:
        return dtype

    header = getattr(signal, "header_text", "") or ""
    full = getattr(signal, "full_text", "") or ""
    size = getattr(signal, "page_size_group", "OTHER") or "OTHER"
    result = get_matcher().match(header, full[:900], page_size_group=size)
    key = (result.doc_type_key or "").upper()
    if key and key in PARTY_DOC_CATALOG:
        return key

    if looks_like_phieu_bo_sung(header, full):
        return "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
    if looks_like_phieu_dang_vien(header, full):
        return "PHIEU_DANG_VIEN"
    from pipeline.doc_identity import looks_like_phieu_xin_y_kien

    if looks_like_phieu_xin_y_kien(header, full):
        return "TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI"
    if looks_like_kiem_diem_header(header, full):
        return "BAN_TU_KIEM_DIEM_HANG_NAM"
    if looks_like_ly_lich_header(header, full):
        return "LY_LICH_DANG_VIEN"
    if looks_like_quyet_dinh_or_nghi_quyet(header, full):
        # Generic bucket for quyết định điều động / công nhận khi matcher miss
        return "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"
    return None


def promote_orphans_to_groups(
    groups: list[DocumentGroup],
    orphan_pages: list[int],
    all_signals: dict[int, PageSignal],
) -> tuple[list[DocumentGroup], list[int], int]:
    """
    Orphan có loại catalog rõ → mở DocumentGroup mới (chuỗi trang liền kề cùng loại).
    Giảm orphan rate khi soft-max/title miss khiến trang bị cách ly.
    """
    if not orphan_pages:
        return groups, orphan_pages, 0

    orphan_set = set(orphan_pages)
    used: set[int] = set()
    new_groups: list[DocumentGroup] = []
    next_id = max((g.group_id for g in groups), default=0) + 1
    promoted = 0

    for pn in sorted(orphan_pages):
        if pn in used:
            continue
        sig = all_signals.get(pn)
        if sig is None or getattr(sig, "is_toc", False):
            continue
        if _looks_like_standalone_minutes(sig):
            continue
        dtype = _infer_orphan_doc_type(sig)
        if not dtype:
            continue

        chain = [pn]
        used.add(pn)
        cur = pn + 1
        while cur in orphan_set and cur not in used:
            sig_n = all_signals.get(cur)
            if sig_n is None or getattr(sig_n, "is_toc", False):
                break
            if _looks_like_standalone_minutes(sig_n):
                break
            dtype_n = _infer_orphan_doc_type(sig_n)
            # Tiếp tục chuỗi nếu cùng loại hoặc chưa nhận loại (mid-page)
            if dtype_n and dtype_n != dtype:
                break
            if dtype_n is None:
                # mid-page: chỉ nối nếu density ổn và chưa chạm soft-max
                max_soft = soft_max_pages_for(dtype)
                if max_soft is not None and len(chain) >= max_soft:
                    break
                if (getattr(sig_n, "text_density", 0) or 0) < 0.01:
                    break
            chain.append(cur)
            used.add(cur)
            cur += 1
            max_soft = soft_max_pages_for(dtype)
            if max_soft is not None and len(chain) >= max_soft:
                break

        g = DocumentGroup(
            group_id=next_id,
            raw_title=(getattr(sig, "header_text", "") or "")[:200],
            doc_type=dtype,
            page_numbers=list(chain),
            page_size_group=getattr(sig, "page_size_group", "OTHER") or "OTHER",
        )
        next_id += 1
        new_groups.append(g)
        promoted += len(chain)
        logger.info(
            f"[promote] orphan pages {chain[0]}-{chain[-1]} → "
            f"group #{g.group_id} type={dtype}"
        )

    if not new_groups:
        return groups, orphan_pages, 0

    remaining = [p for p in orphan_pages if p not in used]
    return list(groups) + new_groups, remaining, promoted
