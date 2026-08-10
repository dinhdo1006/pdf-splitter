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


def scrub_mismatched_form_pages(
    groups: list[DocumentGroup],
    all_signals: dict[int, PageSignal],
) -> tuple[list[DocumentGroup], int]:
    """
    Gỡ trang lệch loại khỏi group form (vd. phiếu ĐV dính vào lý lịch).
    Trang lệch → DocumentGroup mới theo loại suy ra.

    Chỉ peel từ LY_LICH / CHUA_XAC_DINH / PHIEU_* khi gặp header form khác rõ
    (kiểm điểm / phiếu / QĐ). Không peel mid-page yếu.
    """
    from pipeline.doc_identity import (
        looks_like_ke_khai_tai_san,
        looks_like_kiem_diem_header,
        looks_like_phieu_bo_sung,
        looks_like_phieu_dang_vien,
        looks_like_phieu_xin_y_kien,
        looks_like_quyet_dinh_or_nghi_quyet,
    )

    ll_types = {"LY_LICH_DANG_VIEN", "LY_LICH_NGUOI_XIN_VAO_DANG"}
    peel_hosts = ll_types | {
        "CHUA_XAC_DINH",
        "",
        "PHIEU_DANG_VIEN",
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI",
        "BAN_TU_KIEM_DIEM_TAI_THOI_DIEM_CHUYEN",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_VI_PHAM",
    }

    def _strong_kiem_diem(header: str, full: str) -> bool:
        """Chỉ header bản kiểm điểm — tránh dính chữ 'kiểm điểm' mid-page."""
        from unidecode import unidecode
        import re

        blob = unidecode((header or "")[:350]).lower()
        compact = re.sub(r"[\s\-_\.]+", "", blob)
        if "bankiemdiem" in compact or "bantukiemdiem" in compact:
            return True
        return any(
            x in blob
            for x in (
                "ban kiem diem",
                "ban tu kiem diem",
                "ban kiem diem dang vien",
                "ban kiem diem ca nhan",
                "tu kiem diem hang nam",
                "ban tu kien aien",
            )
        )

    def _strong_phieu(header: str, full: str) -> bool:
        return looks_like_phieu_bo_sung(header, full) or looks_like_ke_khai_tai_san(
            header, full
        ) or looks_like_phieu_dang_vien(header, full)

    def _foreign_type(host: str, header: str, full: str) -> str | None:
        host_u = (host or "").upper()
        if host_u not in peel_hosts and not host_u.startswith("PHIEU") and not host_u.startswith(
            "BAN_TU_KIEM"
        ):
            return None
        # Kiểm điểm → peel phiếu / kê khai tài sản / phiếu xin ý kiến
        if host_u.startswith("BAN_TU_KIEM"):
            if looks_like_phieu_dang_vien(header, full):
                return "PHIEU_DANG_VIEN"
            if looks_like_phieu_bo_sung(header, full) or looks_like_ke_khai_tai_san(
                header, full
            ):
                return "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
            if looks_like_phieu_xin_y_kien(header, full):
                return "TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI"
            return None
        # Phiếu → chỉ peel kiểm điểm header mạnh
        if host_u.startswith("PHIEU"):
            if _strong_kiem_diem(header, full) or looks_like_kiem_diem_header(
                header, full
            ):
                # Chỉ peel khi header mạnh / compact OCR, tránh mid-page
                if _strong_kiem_diem(header, full):
                    return "BAN_TU_KIEM_DIEM_HANG_NAM"
            return None
        # CHUA / LL
        if looks_like_phieu_bo_sung(header, full) and "PHIEU_BO_SUNG" not in host_u:
            return "PHIEU_BO_SUNG_HO_SO_DANG_VIEN"
        if looks_like_phieu_dang_vien(header, full) and "PHIEU" not in host_u:
            return "PHIEU_DANG_VIEN"
        if (
            _strong_kiem_diem(header, full)
            or looks_like_kiem_diem_header(header, full)
        ) and not host_u.startswith("BAN_TU_KIEM"):
            # Trên CHUA/LL: nhận cả OCR dính chữ
            if host_u in ll_types or host_u in {"CHUA_XAC_DINH", ""}:
                return "BAN_TU_KIEM_DIEM_HANG_NAM"
        if looks_like_quyet_dinh_or_nghi_quyet(header, full) and host_u in (
            ll_types | {"CHUA_XAC_DINH", ""}
        ):
            return "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM"
        return None

    next_id = max((g.group_id for g in groups), default=0) + 1
    out: list[DocumentGroup] = []
    scrubbed = 0

    for g in groups:
        host = (g.doc_type or "").upper()
        stay: list[int] = []
        pending: list[tuple[str, list[int]]] = []  # (dtype, pages)

        def _flush_pending() -> None:
            nonlocal next_id, scrubbed
            for dtype, pages in pending:
                if not pages:
                    continue
                sig0 = all_signals.get(pages[0])
                ng = DocumentGroup(
                    group_id=next_id,
                    raw_title=(getattr(sig0, "header_text", "") or "")[:200]
                    if sig0
                    else "",
                    doc_type=dtype,
                    page_numbers=list(pages),
                    page_size_group=(
                        getattr(sig0, "page_size_group", g.page_size_group)
                        if sig0
                        else g.page_size_group
                    )
                    or "OTHER",
                )
                next_id += 1
                scrubbed += len(pages)
                out.append(ng)
                logger.warning(
                    f"[audit] Scrub pages {pages[0]}-{pages[-1]} "
                    f"out of group #{g.group_id} ({host}) → {dtype}"
                )
            pending.clear()

        for pn in g.page_numbers:
            sig = all_signals.get(pn)
            header = getattr(sig, "header_text", "") if sig else ""
            full = getattr(sig, "full_text", "") if sig else ""
            foreign = _foreign_type(host, header or "", full or "")

            if foreign:
                # Group mở bằng trang ngoại lai → đổi loại thay vì peel
                if not stay and (
                    host in ll_types or host in {"CHUA_XAC_DINH", ""}
                ):
                    g.doc_type = foreign
                    host = foreign
                    stay.append(pn)
                    continue
                if pending and pending[-1][0] == foreign:
                    pending[-1][1].append(pn)
                else:
                    if pending:
                        _flush_pending()
                    pending.append((foreign, [pn]))
                continue

            if pending:
                from pipeline.continuation_validator import soft_max_pages_for
                from pipeline.doc_identity import looks_like_ly_lich_header

                dtype_p, pages_p = pending[-1]
                max_soft = soft_max_pages_for(dtype_p) or 8
                curr_sg = (
                    getattr(sig, "page_size_group", "OTHER") if sig else "OTHER"
                ) or "OTHER"
                host_sg = g.page_size_group or "OTHER"
                back_to_host = False
                if host in ll_types and looks_like_ly_lich_header(
                    header or "", full or ""
                ):
                    back_to_host = True
                if len(pages_p) >= max_soft:
                    back_to_host = True
                # Booklet LL host + A4 foreign chain rồi lại booklet → đóng foreign
                if (
                    host in ll_types
                    and host_sg in {"BOOKLET_SMALL", "BOOKLET_LANDSCAPE"}
                    and curr_sg in {"BOOKLET_SMALL", "BOOKLET_LANDSCAPE"}
                    and len(pages_p) >= 1
                ):
                    # chỉ đóng nếu đã có ≥1 trang foreign và trang hiện tại booklet
                    prev_sg = (
                        getattr(all_signals.get(pages_p[-1]), "page_size_group", "")
                        or ""
                    )
                    if prev_sg in {"A4_PORTRAIT", "A4_MEDIUM", "OTHER"}:
                        back_to_host = True

                if back_to_host:
                    _flush_pending()
                    stay.append(pn)
                else:
                    pages_p.append(pn)
                continue

            stay.append(pn)

        if pending:
            # Nếu đổi loại cả group (stay chứa foreign vì host đổi) thì không flush
            _flush_pending()

        if stay:
            g.page_numbers = stay
            out.append(g)
        else:
            logger.warning(f"[audit] Drop empty group #{g.group_id} after form scrub")

    return out, scrubbed


def eject_noise_pages_from_unknown(
    groups: list[DocumentGroup],
    all_signals: dict[int, PageSignal],
    orphan_pages: list[int],
) -> tuple[list[DocumentGroup], list[int], int]:
    """
    Đẩy trang biên bản / bàn giao listing ra khỏi nhóm CHUA_XAC_DINH → orphan.
    Giảm KHAC lẫn tạp; biên bản vẫn review qua orphan label BIEN_BAN.
    """
    from pipeline.doc_identity import (
        looks_like_ban_giao_listing,
        looks_like_standalone_minutes,
    )

    orphan_set = set(orphan_pages)
    ejected = 0
    kept: list[DocumentGroup] = []

    for g in groups:
        host = (g.doc_type or "").upper()
        if host not in {"CHUA_XAC_DINH", ""}:
            kept.append(g)
            continue
        stay: list[int] = []
        for pn in g.page_numbers:
            sig = all_signals.get(pn)
            header = getattr(sig, "header_text", "") if sig else ""
            full = getattr(sig, "full_text", "") if sig else ""
            if looks_like_standalone_minutes(header or "", full or "") or (
                looks_like_ban_giao_listing(header or "", full or "")
            ):
                orphan_set.add(pn)
                ejected += 1
                logger.warning(
                    f"[audit] Eject page {pn} from KHAC #{g.group_id} → orphan"
                )
            else:
                stay.append(pn)
        if stay:
            g.page_numbers = stay
            kept.append(g)
        else:
            logger.warning(f"[audit] Drop empty KHAC #{g.group_id} after eject")

    return kept, sorted(orphan_set), ejected


def merge_adjacent_same_year_groups(
    groups: list[DocumentGroup],
    max_pages_by_type: dict[str, int] | None = None,
) -> tuple[list[DocumentGroup], int]:
    """
    Gộp nhóm cùng loại + cùng năm + trang liền nhau (vd. phiếu bổ sung bị soft-max cắt).
    """
    import config as cfg

    caps = max_pages_by_type or getattr(
        cfg, "DOC_TYPE_SAME_YEAR_MERGE_MAX", None
    ) or {
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN": 12,
        "PHIEU_DANG_VIEN": 10,
    }
    if not groups:
        return groups, 0

    ordered = sorted(
        groups,
        key=lambda g: (min(g.page_numbers) if g.page_numbers else 10**9, g.group_id),
    )
    merged: list[DocumentGroup] = []
    n_merged = 0

    for g in ordered:
        dtype = (g.doc_type or "").upper()
        cap = caps.get(dtype)
        if (
            merged
            and cap is not None
            and (merged[-1].doc_type or "").upper() == dtype
            and merged[-1].doc_year is not None
            and g.doc_year is not None
            and merged[-1].doc_year == g.doc_year
            and merged[-1].page_numbers
            and g.page_numbers
            and min(g.page_numbers) == max(merged[-1].page_numbers) + 1
            and len(merged[-1].page_numbers) + len(g.page_numbers) <= cap
        ):
            merged[-1].page_numbers = sorted(
                set(merged[-1].page_numbers) | set(g.page_numbers)
            )
            n_merged += 1
            logger.info(
                f"[merge] group #{g.group_id} → #{merged[-1].group_id} "
                f"{dtype} year={g.doc_year} pages={merged[-1].page_numbers[0]}-"
                f"{merged[-1].page_numbers[-1]}"
            )
        else:
            merged.append(g)

    return merged, n_merged


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
