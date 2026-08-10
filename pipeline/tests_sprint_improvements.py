"""
Smoke / unit tests for page-size, reattach, EOD, alias gate, manifest.
Chạy: python -m pipeline.tests_sprint_improvements
hoặc: python pipeline/tests_sprint_improvements.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as script from pdf_splitter/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from loguru import logger

import config
from pipeline.boundary_detector import BoundaryDetector, DocumentGroup, PageClass
from pipeline.end_of_doc_detector import detect_end_of_doc
from pipeline.manifest_extractor import extract_manifest, validate_output_vs_manifest
from pipeline.orphan_reattacher import reattach_orphans
from pipeline.party_doc_matcher import PartyDocMatcher
from pipeline.signal_extractor import PageSignal


def _sig(
    page_num: int,
    *,
    size: str = "OTHER",
    matched: str = "",
    score: float = 0.0,
    toc: bool = False,
    header: str = "",
    full: str = "",
    eod: bool = False,
    eod_conf: float = 0.0,
) -> PageSignal:
    return PageSignal(
        page_num=page_num,
        header_text=header,
        header_blocks=[],
        full_text=full,
        has_doc_keyword=bool(matched),
        matched_keyword=matched,
        has_large_centered_text=False,
        is_continuation=False,
        text_density=0.2,
        is_blank=False,
        avg_confidence=0.8,
        matched_doc_type=matched,
        match_score=90.0 if matched else 0.0,
        all_blocks=[],
        is_toc=toc,
        page_size_group=size,
        boundary_score=score,
        is_likely_end_of_doc=eod,
        end_of_doc_confidence=eod_conf,
    )


def test_classify_page_size() -> None:
    assert config.classify_page_size(386, 572) == "BOOKLET_SMALL"
    assert config.classify_page_size(528, 405) == "LANDSCAPE_SMALL"
    assert config.classify_page_size(595, 842) == "A4_PORTRAIT"
    assert config.classify_page_size(100, 100) == "OTHER"
    assert config.page_size_ocr_dpi(386, 572) == 300
    print("  OK  classify_page_size")


def test_soft_booklet_continuation() -> None:
    det = BoundaryDetector()
    # Page 1: open booklet group (bootstrap)
    s1 = _sig(1, size="BOOKLET_SMALL", matched="LY_LICH_DANG_VIEN", header="LY LICH")
    d1 = det.process_page(s1)
    assert d1.page_class == PageClass.NEW_DOCUMENT

    # Pages 2-4: same size, no catalog → soft cont
    for pn in (2, 3, 4):
        s = _sig(pn, size="BOOKLET_SMALL", header=f"SECTION {pn}")
        d = det.process_page(s)
        assert d.page_class == PageClass.CONFIRMED_CONTINUATION, (
            f"page {pn} expected cont, got {d.page_class} ({d.reasoning})"
        )

    groups, orphans = det.finalize()
    assert len(groups) == 1
    assert groups[0].page_numbers == [1, 2, 3, 4]
    assert orphans == []
    print("  OK  soft_booklet_continuation")


def test_size_change_is_new() -> None:
    det = BoundaryDetector()
    det.process_page(
        _sig(1, size="LANDSCAPE_SMALL", matched="LY_LICH_DANG_VIEN", header="SO YEU")
    )
    det.process_page(_sig(2, size="LANDSCAPE_SMALL", header="cont"))
    # Size change BOOKLET → should open new (catalog or high score from size change)
    s3 = _sig(3, size="BOOKLET_SMALL", matched="LY_LICH_DANG_VIEN", header="LY LICH")
    d3 = det.process_page(s3)
    assert d3.page_class == PageClass.NEW_DOCUMENT
    groups, _ = det.finalize()
    assert len(groups) == 2
    print("  OK  size_change_is_new")


def test_reattach_sandwich() -> None:
    g = DocumentGroup(
        group_id=1,
        raw_title="LL",
        doc_type="LY_LICH_DANG_VIEN",
        page_numbers=[3, 4, 6],
        page_size_group="BOOKLET_SMALL",
    )
    signals = {
        5: _sig(5, size="BOOKLET_SMALL", score=0.2),
    }
    groups, orphans, decisions = reattach_orphans([g], [5], signals)
    assert 5 in groups[0].page_numbers
    assert orphans == []
    assert decisions[0].action == "attach_prev"
    assert decisions[0].reason.startswith("sandwiched_between_same_group")
    print("  OK  reattach_sandwich")


def test_reattach_sandwich_not_into_quyet_dinh_minutes() -> None:
    g = DocumentGroup(
        group_id=1,
        raw_title="QD",
        doc_type="QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC",
        page_numbers=[28, 30],
        page_size_group="A4_MEDIUM",
    )
    signals = {
        29: _sig(
            29,
            size="A4_MEDIUM",
            score=0.15,
            header="TRICH BIEN BAN HOP CHI BO",
            full="xet chuyen dang chinh thuc cho dong chi ...",
        )
    }
    groups, orphans, decisions = reattach_orphans([g], [29], signals)
    assert groups[0].page_numbers == [28, 30]
    assert orphans == [29]
    assert decisions[0].reason.startswith("no_sandwich_into_quyet_dinh")
    print("  OK  reattach_sandwich_not_into_quyet_dinh_minutes")


def test_quyet_dinh_not_swallow_bien_ban() -> None:
    det = BoundaryDetector()
    d1 = det.process_page(
        _sig(
            28,
            size="A4_MEDIUM",
            matched="QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC",
            header="SO: 07 QN-DB NGHI QUYET",
            eod=True,
            eod_conf=0.9,
        )
    )
    assert d1.page_class == PageClass.NEW_DOCUMENT
    d2 = det.process_page(
        _sig(
            29,
            size="A4_MEDIUM",
            header="TRICH BIEN BAN HOP CHI BO",
            full="xet chuyen dang chinh thuc Pham Huu Luat",
        )
    )
    assert d2.page_class == PageClass.ORPHAN_PAGE
    # Trang tiếp không được soft-cont vào QĐ đã đóng
    d3 = det.process_page(
        _sig(30, size="A4_MEDIUM", header="Phan 3 y kien tham gia", full="y kien 1 ...")
    )
    assert d3.page_class != PageClass.CONFIRMED_CONTINUATION
    groups, orphans = det.finalize()
    qd = next(g for g in groups if g.doc_type and g.doc_type.startswith("QUYET_DINH"))
    assert qd.page_numbers == [28]
    assert 29 in orphans
    print("  OK  quyet_dinh_not_swallow_bien_ban")


def test_reattach_case4_size() -> None:
    # prev không phải MULTI_PAGE_FORM / QĐ → Case 2 không khớp; Case 4 size khớp
    g = DocumentGroup(
        group_id=1,
        raw_title="GT",
        doc_type="GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC",
        page_numbers=[3, 4],
        page_size_group="BOOKLET_SMALL",
    )
    signals = {5: _sig(5, size="BOOKLET_SMALL", score=0.2)}
    groups, orphans, decisions = reattach_orphans([g], [5], signals)
    assert 5 in groups[0].page_numbers
    assert decisions[0].reason.startswith("same_page_size_group_as_prev")
    print("  OK  reattach_case4_size")


def test_reattach_trailing_phieu_dang_vien_page() -> None:
    g = DocumentGroup(
        group_id=1,
        raw_title="PHIEU",
        doc_type="PHIEU_DANG_VIEN",
        page_numbers=[21],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        22: _sig(
            22,
            size="A4_PORTRAIT",
            score=0.15,
            header="22) TOM TAT QUA TRINH",
            full="1985-1987 hoc tai truong ...",
        )
    }
    groups, orphans, decisions = reattach_orphans([g], [22], signals)
    assert 22 in groups[0].page_numbers
    assert orphans == []
    assert decisions[0].reason.startswith("trailing_page_of_multi_page_form")
    print("  OK  reattach_trailing_phieu_dang_vien_page")


def test_eod_signature_label() -> None:
    class B:
        def __init__(self, text: str, y: float):
            self.text = text
            self.bbox = (10, y, 100, y + 20)
            self.confidence = 0.9

    blocks = [B("Noi dung", 50), B("Ky ten Bi Thu", 800)]
    # Fake tall page so y=800 is in bottom 28%
    eod = detect_end_of_doc(None, blocks, page_height_px=1000)
    assert eod.has_signature_label
    assert eod.is_end_of_doc
    print("  OK  eod_signature_label")


def test_alias_size_gate() -> None:
    m = PartyDocMatcher()
    # Short "ly lich" only on BOOKLET
    r_ok = m.match("LY LICH", "", page_size_group="BOOKLET_SMALL")
    assert r_ok.doc_type_key == "LY_LICH_DANG_VIEN", r_ok
    r_no = m.match("LY LICH", "", page_size_group="A4_PORTRAIT")
    assert r_no.doc_type_key == "", r_no
    # Longer alias works everywhere
    r2 = m.match("BAN TU KIEM DIEM HANG NAM", "", page_size_group="A4_PORTRAIT")
    assert r2.doc_type_key == "BAN_TU_KIEM_DIEM_HANG_NAM"
    print("  OK  alias_size_gate")


def test_manifest_toc() -> None:
    text = (
        "MUC LUC TAI LIEU TRONG HO SO DANG VIEN\n"
        "Ly lich dang vien Co\n"
        "Phieu dang vien Co\n"
        "Quyet dinh ket nap dang vien Khong\n"
    )
    m = extract_manifest(20, text, "MUC LUC TAI LIEU")
    assert m is not None
    assert m.source_page == 20
    val = validate_output_vs_manifest(m, ["LY_LICH_DANG_VIEN", "PHIEU_DANG_VIEN"])
    assert "completeness_pct" in val
    print("  OK  manifest_toc")


def test_toc_never_into_ly_lich() -> None:
    det = BoundaryDetector()
    det.process_page(
        _sig(1, size="BOOKLET_SMALL", matched="LY_LICH_DANG_VIEN", header="LY LICH")
    )
    det.process_page(_sig(2, size="BOOKLET_SMALL", header="cont"))
    # TOC on A4 after booklet
    toc = _sig(3, size="A4_PORTRAIT", toc=True, header="MUC LUC TAI LIEU")
    d = det.process_page(toc)
    assert d.page_class == PageClass.ORPHAN_PAGE
    groups, orphans = det.finalize()
    assert 3 in orphans
    # booklet closed before TOC — only pages 1-2 in ly lich
    assert any(g.page_numbers == [1, 2] for g in groups)
    print("  OK  toc_never_into_ly_lich")


def test_booklet_to_a4_hard_boundary() -> None:
    det = BoundaryDetector()
    det.process_page(
        _sig(1, size="BOOKLET_SMALL", matched="LY_LICH_DANG_VIEN", header="LY LICH")
    )
    det.process_page(_sig(2, size="BOOKLET_SMALL", header="p2"))
    # A4 with phieu-like match should be NEW separate group
    s3 = _sig(3, size="A4_PORTRAIT", matched="PHIEU_DANG_VIEN", header="MAU 2 HSDV")
    d3 = det.process_page(s3)
    assert d3.page_class == PageClass.NEW_DOCUMENT
    groups, _ = det.finalize()
    assert len(groups) == 2
    assert groups[0].page_numbers == [1, 2]
    assert groups[1].page_numbers == [3]
    assert groups[1].doc_type == "PHIEU_DANG_VIEN"
    print("  OK  booklet_to_a4_hard_boundary")


def test_page_audit_complete() -> None:
    from pipeline.page_audit import audit_page_coverage

    g = DocumentGroup(
        group_id=1,
        raw_title="x",
        doc_type="LY_LICH_DANG_VIEN",
        page_numbers=[1, 2, 3],
        page_size_group="BOOKLET_SMALL",
    )
    report = audit_page_coverage(5, [g], [4], [5])
    assert report["complete"] is True
    assert report["coverage_pct"] == 100.0
    report2 = audit_page_coverage(5, [g], [], [])
    assert report2["missing_pages"] == [4, 5]
    print("  OK  page_audit_complete")


def test_so_ly_lich_not_on_a4_phieu() -> None:
    m = PartyDocMatcher()
    r = m.match(
        "MU 2 - HSDV\nSO LY LICH: 123",
        "",
        page_size_group="A4_PORTRAIT",
    )
    assert r.doc_type_key == "PHIEU_DANG_VIEN", r
    print("  OK  so_ly_lich_not_on_a4_phieu")


def test_bien_ban_not_kiem_diem() -> None:
    m = PartyDocMatcher()
    r = m.match("BIEN BAN CHI BO", "", page_size_group="OTHER")
    assert r.source == "appendix"
    assert r.doc_type_key == ""
    print("  OK  bien_ban_not_kiem_diem")


def test_force_phieu_and_quyet_dinh_split() -> None:
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "PHIEU_DANG_VIEN",
        None,
        None,
        1,
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        "PHIEU BO SUNG HO SO",
        "",
    )
    assert force and "bo_sung" in reason

    force_same, _ = should_force_new_document(
        "PHIEU_DANG_VIEN",
        None,
        None,
        1,
        "PHIEU_DANG_VIEN",
        "TOM TAT QUA TRINH CONG TAC",
        "",
    )
    assert force_same is False

    force2, reason2 = should_force_new_document(
        "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC",
        1995,
        "05/QN-DB",
        1,
        "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC",
        "SO: 12/QN-DB",
        "Quyet dinh so 12/QN-DB",
    )
    assert force2
    print("  OK  force_phieu_and_quyet_dinh_split")


def test_manifest_without_co_khong() -> None:
    text = (
        "MUC LUC TAI LIEU TRONG HO SO DANG VIEN\n"
        "01. Ly lich dang vien\n"
        "03. Phieu dang vien\n"
        "04. Phieu bo sung ho so dang vien\n"
    )
    m = extract_manifest(20, text, "MUC LUC TAI LIEU")
    assert m is not None
    assert len(m.entries) >= 2, m.entries
    print("  OK  manifest_without_co_khong")


def test_prev_eod_boost() -> None:
    det = BoundaryDetector()
    det.process_page(
        _sig(
            1,
            size="A4_PORTRAIT",
            matched="QUYET_DINH_KET_NAP_DANG_VIEN",
            header="QD",
            eod=True,
            eod_conf=0.85,
        )
    )
    s2 = _sig(2, size="A4_PORTRAIT", header="GIAY GIOI THIEU SINH HOAT DANG")
    score, reason = det._compute_score(s2)
    assert "prev_eod" in reason or score >= 0.0
    det._prev_signal = _sig(1, eod=True, eod_conf=0.9, size="A4_PORTRAIT")
    score2, reason2 = det._compute_score(
        _sig(2, size="A4_PORTRAIT", header="X")
    )
    assert "prev_eod" in reason2
    assert score2 >= 0.35
    print("  OK  prev_eod_boost")


def test_identity_extract_from_phieu_and_cli_override() -> None:
    from unidecode import unidecode

    from pipeline.identity_extractor import (
        apply_cli_overrides,
        extract_member_identity_from_text,
    )

    text = (
        "DANG CONG SAN VIET NAM\n"
        "MAU 2 - HSDV\n"
        "PHIEU DANG VIEN\n"
        "01) Ho va ten khai sinh: Pham Huu Luat\n"
        "So CCCD: 001234567890\n"
    )
    ident = extract_member_identity_from_text(text, source="phieu")
    assert ident.ho_ten is not None
    assert "Luat" in ident.ho_ten or "luat" in ident.ho_ten.lower()
    assert ident.cccd == "001234567890"

    toc = "MUC LUC Tai lieu trong ho so dang vien cua dong chi: Nguyen Van Anh"
    ident2 = extract_member_identity_from_text(toc, source="toc")
    assert ident2.ho_ten is not None
    assert "Nguyen" in unidecode(ident2.ho_ten)

    merged = apply_cli_overrides(ident, ho_ten="Tran Thi B", cccd=None)
    assert merged.ho_ten == "Tran Thi B"
    assert merged.cccd == "001234567890"
    print("  OK  identity_extract_from_phieu_and_cli_override")


def test_year_from_thang_nam_and_ocr_blob() -> None:
    from pipeline.year_aware_sequencer import (
        DocRecord,
        YearAwareSequencer,
        extract_year_robust,
    )

    assert extract_year_robust("Ha Noi, ngay 06 thang 01 nam 1995") == 1995
    assert extract_year_robust("ban hanh ngay 15/11/1994") == 1994

    seq = YearAwareSequencer()
    records = [
        DocRecord(
            doc_type_key="BAN_TU_KIEM_DIEM_HANG_NAM",
            raw_title="BAN TU KIEM DIEM",
            page_numbers=[1],
            pdf_order=0,
            ocr_blob="Nam 2018 kiem diem dang vien",
        ),
        DocRecord(
            doc_type_key="BAN_TU_KIEM_DIEM_HANG_NAM",
            raw_title="BAN TU KIEM DIEM",
            page_numbers=[2],
            pdf_order=1,
            ocr_blob="Nam 2020 kiem diem dang vien",
        ),
    ]
    seq.assign_sequence(records)
    by_order = {r.pdf_order: r for r in records}
    assert by_order[0].sequence_number == 1
    assert by_order[1].sequence_number == 2
    assert by_order[0].doc_year == 2018
    assert by_order[1].doc_year == 2020
    print("  OK  year_from_thang_nam_and_ocr_blob")


def test_orphan_review_namer() -> None:
    from pipeline.review_namer import orphan_review_filename

    toc = _sig(20, toc=True, header="MUC LUC TAI LIEU")
    assert orphan_review_filename(20, toc).startswith("MUC_LUC_page_0020")

    bb = _sig(
        29,
        header="TRICH BIEN BAN HOP CHI BO",
        full="xet chuyen dang chinh thuc",
    )
    assert orphan_review_filename(29, bb).startswith("BIEN_BAN_page_0029")

    plain = _sig(40, header="xyz")
    assert orphan_review_filename(40, plain).startswith("ORPHAN_page_0040")
    print("  OK  orphan_review_namer")


def test_identity_reject_tdv_and_garbage_name() -> None:
    from pipeline.identity_extractor import extract_member_identity_from_text

    tdv = (
        "PHIEU DANG VIEN\n"
        "So TDV: 2772694\n"
        "Ho va ten khai sinh: Lacain Ahang T\n"
    )
    bad = extract_member_identity_from_text(tdv, source="bad")
    assert bad.cccd is None, bad
    assert bad.ho_ten is None, bad

    garbage = extract_member_identity_from_text(
        "Ho va ten: Pham Hay Ack Bi Danh\nSo TDV: 2772699\n",
        source="garbage",
    )
    assert garbage.ho_ten is None, garbage

    bi_danh = extract_member_identity_from_text(
        "Ho va ten: Pham Huu Luat Bi danh: ABC\nSo CCCD: 001234567890\n",
        source="bidanh",
    )
    assert bi_danh.ho_ten is not None and "Luat" in bi_danh.ho_ten
    assert "Bi" not in (bi_danh.ho_ten or "")
    assert bi_danh.cccd == "001234567890"

    good = extract_member_identity_from_text(
        "Ho va ten khai sinh: Pham Huu Luat\nSo CCCD: 001234567890\n",
        source="good",
    )
    assert good.ho_ten is not None and "Luat" in good.ho_ten
    assert good.cccd == "001234567890"
    print("  OK  identity_reject_tdv_and_garbage_name")


def test_soft_max_ly_lich_and_toc_closes_kiem_diem() -> None:
    from pipeline.continuation_validator import soft_max_pages_for

    assert soft_max_pages_for("LY_LICH_DANG_VIEN") == 18
    assert soft_max_pages_for("PHIEU_BO_SUNG_HO_SO_DANG_VIEN") == 6

    det = BoundaryDetector()
    det.process_page(
        _sig(
            1,
            size="BOOKLET_SMALL",
            matched="LY_LICH_DANG_VIEN",
            header="LY LICH DANG VIEN",
            full="trang 1",
        )
    )
    for pn in range(2, 20):
        d = det.process_page(
            _sig(
                pn,
                size="BOOKLET_SMALL",
                header="tiep",
                full=f"trang {pn} noi dung form",
            )
        )
        if pn <= 18:
            assert d.page_class == PageClass.CONFIRMED_CONTINUATION, (
                pn,
                d.reasoning,
            )
        else:
            # Chạm soft-max → orphan hoặc NEW, không nuốt tiếp
            assert d.page_class != PageClass.CONFIRMED_CONTINUATION, (
                pn,
                d.reasoning,
            )
    groups, orphans = det.finalize()
    ll = [g for g in groups if g.doc_type == "LY_LICH_DANG_VIEN"]
    assert ll and len(ll[0].page_numbers) <= 18, ll[0].page_numbers

    det2 = BoundaryDetector()
    det2.process_page(
        _sig(
            1,
            size="A4_PORTRAIT",
            matched="BAN_TU_KIEM_DIEM_HANG_NAM",
            header="BAN TU KIEM DIEM",
            full="nam 2012",
        )
    )
    det2.process_page(
        _sig(2, size="A4_PORTRAIT", header="tiep", full="noi dung kiem diem")
    )
    d_toc = det2.process_page(
        _sig(3, size="A4_PORTRAIT", toc=True, header="MUC LUC TAI LIEU")
    )
    assert d_toc.page_class == PageClass.ORPHAN_PAGE
    d4 = det2.process_page(
        _sig(4, size="A4_PORTRAIT", header="noi dung bat ky")
    )
    assert d4.page_class == PageClass.ORPHAN_PAGE
    g2, o2 = det2.finalize()
    assert any(g.page_numbers == [1, 2] for g in g2), g2
    assert 3 in o2 and 4 in o2
    print("  OK  soft_max_ly_lich_and_toc_closes_kiem_diem")


def test_refine_unknown_group_types() -> None:
    from pipeline.party_doc_matcher import refine_unknown_group_types

    g = DocumentGroup(
        group_id=10,
        page_numbers=[59, 60],
        doc_type="CHUA_XAC_DINH",
        page_size_group="A4_MEDIUM",
        raw_title="",
    )
    signals = {
        59: _sig(
            59,
            size="A4_MEDIUM",
            header="1 QUYEN LY LICH DANG VIEN\n1 BIEN BAN HOP CHI BO",
            full="muc luc tai lieu",
        ),
        60: _sig(60, size="A4_MEDIUM", header="tiep", full="noi dung"),
    }
    # TOC-like listing may stay unknown; QĐ header should refine
    g2 = DocumentGroup(
        group_id=11,
        page_numbers=[42],
        doc_type="CHUA_XAC_DINH",
        page_size_group="A4_PORTRAIT",
        raw_title="",
    )
    signals[42] = _sig(
        42,
        size="A4_PORTRAIT",
        header="THANH UY HA NOI\nSO 6294 - QD/TU",
        full="quyet dinh dieu dong",
    )
    n = refine_unknown_group_types([g, g2], signals)
    assert g2.doc_type == "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM", g2.doc_type
    assert n >= 1
    print("  OK  refine_unknown_group_types")


def test_orphan_closes_phieu_dang_vien() -> None:
    from pipeline.boundary_detector import BoundaryDetector, PageClass

    det = BoundaryDetector()
    det.process_page(
        _sig(
            1,
            size="A4_PORTRAIT",
            matched="PHIEU_DANG_VIEN",
            header="MAU 2 HSDV",
            full="Trang 1/3 noi dung phieu",
        )
    )
    d2 = det.process_page(
        _sig(
            2,
            size="A4_PORTRAIT",
            header="tiep theo",
            full="Trang 2/3 noi dung phieu",
        )
    )
    assert d2.page_class == PageClass.CONFIRMED_CONTINUATION, d2.reasoning
    # TOC orphan phải đóng phiếu — trang sau không soft-cont vào phiếu
    d3 = det.process_page(
        _sig(3, size="A4_PORTRAIT", toc=True, header="MUC LUC TAI LIEU TRONG HO SO")
    )
    assert d3.page_class == PageClass.ORPHAN_PAGE
    d4 = det.process_page(
        _sig(4, size="A4_PORTRAIT", header="NOI DUNG BAT KY KHONG CATALOG")
    )
    assert d4.page_class == PageClass.ORPHAN_PAGE
    groups, orphans = det.finalize()
    assert any(g.page_numbers == [1, 2] for g in groups), groups
    assert 3 in orphans and 4 in orphans
    print("  OK  orphan_closes_phieu_dang_vien")


def test_qd_tu_alias_and_chuan_y() -> None:
    m = PartyDocMatcher()
    r = m.match("THANH UY HA NOI\nSO 6294 - QD/TU", "")
    assert r.doc_type_key == "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM", r
    r2 = m.match(
        "NGHI QUYET\nVe viec chuan y cong nhan dang vien chinh thuc",
        "",
    )
    assert r2.doc_type_key == "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC", r2
    print("  OK  qd_tu_alias_and_chuan_y")


def test_force_phieu_bo_sung_year_and_kiem_diem() -> None:
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        2015,
        None,
        3,
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        "MAU 3 HSDV PHIEU BO SUNG nam 2018",
        "Ha Noi, nam 2018",
    )
    assert force and "year" in reason, (force, reason)

    force2, reason2 = should_force_new_document(
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        2012,
        None,
        8,
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN TU KIEM DIEM HANG NAM",
        "Nam 2016",
    )
    assert force2, (force2, reason2)
    print("  OK  force_phieu_bo_sung_year_and_kiem_diem")


def test_phieu_numbered_fields_not_form_section() -> None:
    m = PartyDocMatcher()
    r = m.match(
        "01) Ho va ten khai sinh: Pham Huu Luat",
        "MAU 2 - HSDV\nPHIEU DANG VIEN\n01) Ho va ten",
        page_size_group="A4_PORTRAIT",
    )
    assert r.source != "form_section", r
    assert r.doc_type_key == "PHIEU_DANG_VIEN", r
    print("  OK  phieu_numbered_fields_not_form_section")


def test_form_section_continues_into_phieu() -> None:
    det = BoundaryDetector()
    det.process_page(
        _sig(1, size="A4_PORTRAIT", matched="PHIEU_DANG_VIEN", header="MAU 2 HSDV")
    )
    s2 = _sig(2, size="A4_PORTRAIT", header="01) Ho va ten")
    s2.is_form_section = True
    d2 = det.process_page(s2)
    assert d2.page_class == PageClass.CONFIRMED_CONTINUATION, d2.reasoning
    groups, orphans = det.finalize()
    assert groups[0].page_numbers == [1, 2]
    assert 2 not in orphans
    print("  OK  form_section_continues_into_phieu")


def test_multipass_sandwich_reattach() -> None:
    from pipeline.orphan_reattacher import reattach_orphans

    g = DocumentGroup(
        group_id=1,
        raw_title="phieu",
        doc_type="PHIEU_DANG_VIEN",
        page_numbers=[21],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        21: _sig(21, matched="PHIEU_DANG_VIEN", header="MAU 2"),
        22: _sig(22, header="p2", score=0.1),
        23: _sig(23, header="p3", score=0.1),
        24: _sig(24, header="p4", score=0.1),
    }
    groups, orphans, decisions = reattach_orphans(
        [g], [22, 23, 24], signals
    )
    assert 22 not in orphans and 23 not in orphans and 24 not in orphans, (
        orphans,
        decisions,
    )
    assert groups[0].page_numbers == [21, 22, 23, 24]
    print("  OK  multipass_sandwich_reattach")


def test_reattach_respects_soft_max() -> None:
    from pipeline.orphan_reattacher import reattach_orphans

    g = DocumentGroup(
        group_id=1,
        raw_title="LL",
        doc_type="LY_LICH_DANG_VIEN",
        page_numbers=list(range(1, 19)),  # 18 pages = soft max
        page_size_group="BOOKLET_SMALL",
    )
    signals = {
        pn: _sig(pn, size="BOOKLET_SMALL", header="tiep", full="noi dung")
        for pn in range(1, 22)
    }
    groups, orphans, decisions = reattach_orphans([g], [19, 20, 21], signals)
    assert groups[0].page_numbers == list(range(1, 19)), groups[0].page_numbers
    assert 19 in orphans and 20 in orphans and 21 in orphans
    assert any(
        "max" in (d.reason or "").lower() for d in decisions
    ), [d.reason for d in decisions]
    print("  OK  reattach_respects_soft_max")


def test_promote_orphans_to_groups() -> None:
    from pipeline.orphan_reattacher import promote_orphans_to_groups

    signals = {
        150: _sig(
            150,
            size="A4_PORTRAIT",
            header="MAU 3-HSDV\nPHIEU BO SUNG",
            full="Ho so dang vien",
        ),
        151: _sig(151, size="A4_PORTRAIT", header="tiep", full="noi dung phieu"),
        152: _sig(152, size="A4_PORTRAIT", toc=True, header="MUC LUC"),
    }
    groups, orphans, n = promote_orphans_to_groups([], [150, 151, 152], signals)
    assert n >= 1
    assert any(
        g.doc_type == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN" for g in groups
    ), groups
    assert 152 in orphans  # TOC giữ orphan
    print("  OK  promote_orphans_to_groups")


def test_identity_dot_ocr_name() -> None:
    from pipeline.identity_extractor import extract_member_identity_from_text

    got = extract_member_identity_from_text(
        "Ho va ten: Pham. Huu. Luat\nSo CCCD: 001234567890\n",
        source="dots",
    )
    assert got.ho_ten is not None and "Luat" in got.ho_ten, got
    assert got.cccd == "001234567890"
    print("  OK  identity_dot_ocr_name")


def test_identity_pham_huu_luat_and_tdv_fallback() -> None:
    from pipeline.identity_extractor import extract_member_identity_from_text

    stuck = extract_member_identity_from_text(
        "Ho va ten (viet chu in hoa): PHAM HUU LUATSinh ngay 26 thang 3 nam 1966\n"
        "So TDV: 2772699\n",
        source="stuck",
    )
    assert stuck.ho_ten is not None and "Luat" in stuck.ho_ten, stuck
    assert stuck.tdv == "2772699"
    assert stuck.has_member_folder_keys
    assert stuck.folder_id == "2772699"

    caps = extract_member_identity_from_text(
        "PHAM HUY LUAT\nHo va ten (vit ch in hoa):\nSinh ngay 26\nSo TDV: 5028110\n",
        source="caps",
    )
    assert caps.ho_ten is not None and "Pham" in caps.ho_ten, caps
    print("  OK  identity_pham_huu_luat_and_tdv_fallback")


def test_absorb_trailing_after_soft_max() -> None:
    from pipeline.orphan_reattacher import absorb_trailing_orphans_after_capped_forms

    g = DocumentGroup(
        group_id=1,
        raw_title="phieu",
        doc_type="PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        page_numbers=[144, 145, 146, 147, 148, 149],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        pn: _sig(pn, size="A4_PORTRAIT", header="tai san", full="nha dat gia tri")
        for pn in range(144, 157)
    }
    groups, orphans, n = absorb_trailing_orphans_after_capped_forms(
        [g], list(range(150, 157)), signals
    )
    assert n >= 1
    assert any(150 in gg.page_numbers for gg in groups)
    assert 150 not in orphans
    print("  OK  absorb_trailing_after_soft_max")


def test_reattach_phieu_not_across_toc_into_ly_lich() -> None:
    """Phiếu ĐV sau TOC orphan không được Case-3 gắn vào lý lịch booklet."""
    from pipeline.orphan_reattacher import reattach_orphans

    g = DocumentGroup(
        group_id=2,
        raw_title="LL",
        doc_type="LY_LICH_DANG_VIEN",
        page_numbers=list(range(3, 20)),
        page_size_group="BOOKLET_SMALL",
    )
    signals = {
        pn: _sig(pn, size="BOOKLET_SMALL", header="ly lich", full="noi dung")
        for pn in range(3, 20)
    }
    signals[20] = _sig(20, size="A4_PORTRAIT", toc=True, header="MUC LUC")
    signals[21] = _sig(
        21,
        size="A4_PORTRAIT",
        header="MU 2 - SV\nPHIEU\nDANG VIEN",
        full="SO TDV: 2772699\n01) Ho va ten",
        score=0.2,
    )
    groups, orphans, decisions = reattach_orphans([g], [20, 21], signals)
    assert 21 in orphans, (orphans, decisions)
    assert 21 not in groups[0].page_numbers
    assert any(
        d.orphan_page_num == 21
        and d.action == "keep_orphan"
        for d in decisions
    ), [d for d in decisions if d.orphan_page_num == 21]
    print("  OK  reattach_phieu_not_across_toc_into_ly_lich")


def test_scrub_phieu_out_of_ly_lich() -> None:
    from pipeline.page_audit import scrub_mismatched_form_pages

    g = DocumentGroup(
        group_id=2,
        raw_title="LL",
        doc_type="LY_LICH_DANG_VIEN",
        page_numbers=[3, 4, 5, 21, 22, 23],
        page_size_group="BOOKLET_SMALL",
    )
    signals = {
        3: _sig(3, size="BOOKLET_SMALL", header="LY LICH DANG VIEN", full="a"),
        4: _sig(4, size="BOOKLET_SMALL", header="muc 2", full="b"),
        5: _sig(5, size="BOOKLET_SMALL", header="muc 3", full="c"),
        21: _sig(
            21,
            size="A4_PORTRAIT",
            header="MU 2 - SV\nPHIEU DANG VIEN",
            full="SO TDV 2772699",
        ),
        22: _sig(22, size="A4_PORTRAIT", header="22) Tom tat qua trinh", full="d"),
        23: _sig(23, size="A4_PORTRAIT", header="23) Dao tao", full="e"),
    }
    groups, n = scrub_mismatched_form_pages([g], signals)
    assert n >= 1
    ll = next(gg for gg in groups if gg.doc_type == "LY_LICH_DANG_VIEN")
    assert 21 not in ll.page_numbers
    assert ll.page_numbers == [3, 4, 5]
    phieu = next(gg for gg in groups if gg.doc_type == "PHIEU_DANG_VIEN")
    assert phieu.page_numbers == [21, 22, 23]
    print("  OK  scrub_phieu_out_of_ly_lich")


def test_merge_adjacent_same_year_phieu() -> None:
    from pipeline.page_audit import merge_adjacent_same_year_groups

    g1 = DocumentGroup(
        group_id=1,
        raw_title="p1",
        doc_type="PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        page_numbers=[100, 101, 102, 103, 104, 105],
        page_size_group="A4_PORTRAIT",
        doc_year=2009,
    )
    g2 = DocumentGroup(
        group_id=2,
        raw_title="p2",
        doc_type="PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        page_numbers=[106, 107, 108, 109],
        page_size_group="A4_PORTRAIT",
        doc_year=2009,
    )
    g3 = DocumentGroup(
        group_id=3,
        raw_title="p3",
        doc_type="PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        page_numbers=[120, 121],
        page_size_group="A4_PORTRAIT",
        doc_year=2011,
    )
    groups, n = merge_adjacent_same_year_groups([g1, g2, g3])
    assert n == 1
    assert len(groups) == 2
    assert groups[0].page_numbers == list(range(100, 110))
    assert groups[1].page_numbers == [120, 121]
    print("  OK  merge_adjacent_same_year_phieu")


def test_soft_max_does_not_copy_phieu_onto_kiem_diem() -> None:
    """Sau soft-max phiếu, trang BAN KIEM DIEM phải NEW đúng loại — không copy PHIEU."""
    det = BoundaryDetector()
    for i in range(1, 7):
        det.process_page(
            _sig(
                i,
                size="A4_PORTRAIT",
                matched="PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                header="MAU 3 PHIEU BO SUNG",
                full="phieu bo sung ho so",
            )
        )
    d7 = det.process_page(
        _sig(
            7,
            size="A4_PORTRAIT",
            header="BAN KIEM DIEM DANG VIEN NAM 2012",
            full="Ho va ten: Pham Huu Luat",
            score=0.2,
        )
    )
    assert d7.page_class == PageClass.NEW_DOCUMENT, d7.reasoning
    groups, _ = det.finalize()
    assert any(
        g.doc_type == "BAN_TU_KIEM_DIEM_HANG_NAM" and 7 in g.page_numbers
        for g in groups
    ), [(g.doc_type, g.page_numbers) for g in groups]
    assert not any(
        g.doc_type.startswith("PHIEU") and 7 in g.page_numbers for g in groups
    )
    print("  OK  soft_max_does_not_copy_phieu_onto_kiem_diem")


def test_year_ignores_ngay_sinh() -> None:
    from pipeline.year_aware_sequencer import extract_year_robust

    assert (
        extract_year_robust(
            "BAN KIEM DIEM CA NHAN NAM 2018\nHo va ten: Pham\nNgay sinh: 26/03/1966"
        )
        == 2018
    )
    assert (
        extract_year_robust("Ho va ten Pham\nNgay sinh: 26/03/1966\nSoc Son")
        != 1966
    )
    print("  OK  year_ignores_ngay_sinh")


def test_force_phieu_to_kiem_diem_without_catalog() -> None:
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        2011,
        None,
        3,
        None,
        "BAN KIEM DIEM DANG VIEN",
        "Nam 2012",
    )
    assert force and "kiem_diem" in reason, (force, reason)
    print("  OK  force_phieu_to_kiem_diem_without_catalog")


def test_kiem_diem_does_not_swallow_ke_khai_tai_san() -> None:
    """Kiểm điểm không nuốt Bản kê khai tài sản / phiếu bổ sung."""
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        2017,
        None,
        5,
        None,
        "BAN KE KHAI TAI SAN, THU NHAP\nNAM 2017",
        "Nguoi ke khai tai san",
    )
    assert force and "phieu" in reason, (force, reason)

    det = BoundaryDetector()
    for i in range(1, 6):
        det.process_page(
            _sig(
                i,
                size="A4_PORTRAIT",
                matched="BAN_TU_KIEM_DIEM_HANG_NAM",
                header="BAN KIEM DIEM DANG VIEN NAM 2017",
                full="tu kiem diem",
            )
        )
    d6 = det.process_page(
        _sig(
            6,
            size="A4_PORTRAIT",
            header="BAN KE KHAI TAI SAN, THU NHAP NAM 2017",
            full="Nguoi ke khai tai san: Pham Huu Luat",
            score=0.2,
        )
    )
    assert d6.page_class == PageClass.NEW_DOCUMENT, d6.reasoning
    groups, _ = det.finalize()
    kd = next(g for g in groups if g.doc_type.startswith("BAN_TU_KIEM"))
    assert 6 not in kd.page_numbers
    assert any(
        g.doc_type == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN" and 6 in g.page_numbers
        for g in groups
    ), [(g.doc_type, g.page_numbers) for g in groups]
    print("  OK  kiem_diem_does_not_swallow_ke_khai_tai_san")


def test_scrub_ke_khai_out_of_kiem_diem() -> None:
    from pipeline.page_audit import scrub_mismatched_form_pages

    g = DocumentGroup(
        group_id=1,
        raw_title="KD",
        doc_type="BAN_TU_KIEM_DIEM_HANG_NAM",
        page_numbers=[155, 156, 160, 161],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        155: _sig(
            155,
            header="BAN KIEM DIEM DANG VIEN NAM 2017",
            full="a",
            size="A4_PORTRAIT",
        ),
        156: _sig(156, header="tac phong", full="b", size="A4_PORTRAIT"),
        160: _sig(
            160,
            header="BAN KE KHAI TAI SAN, THU NHAP NAM 2017",
            full="Nguoi ke khai",
            size="A4_PORTRAIT",
        ),
        161: _sig(161, header="Nha o", full="gia tri", size="A4_PORTRAIT"),
    }
    groups, n = scrub_mismatched_form_pages([g], signals)
    assert n >= 1
    kd = next(g for g in groups if g.doc_type.startswith("BAN_TU_KIEM"))
    assert 160 not in kd.page_numbers and 161 not in kd.page_numbers
    assert any(
        g.doc_type == "PHIEU_BO_SUNG_HO_SO_DANG_VIEN" and 160 in g.page_numbers
        for g in groups
    )
    print("  OK  scrub_ke_khai_out_of_kiem_diem")


def test_jammed_ocr_kiem_diem_and_refine_khac() -> None:
    from pipeline.doc_identity import looks_like_kiem_diem_header
    from pipeline.party_doc_matcher import refine_unknown_group_types

    assert looks_like_kiem_diem_header("BANKIEMDIEM\nDANGVIEN", "")
    assert looks_like_kiem_diem_header("BAN TU KIEN AIEN DANG VIEN NAM1998", "")

    g = DocumentGroup(
        group_id=12,
        raw_title="khac",
        doc_type="CHUA_XAC_DINH",
        page_numbers=[60, 61, 62],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        60: _sig(60, header="BANKIEMDIEM DANGVIEN", full="Pham Huu Luat"),
        61: _sig(61, header="chuyen mon", full="hoc tap"),
        62: _sig(62, header="tiep", full="phe binh"),
    }
    n = refine_unknown_group_types([g], signals)
    assert n >= 1
    assert g.doc_type == "BAN_TU_KIEM_DIEM_HANG_NAM"
    print("  OK  jammed_ocr_kiem_diem_and_refine_khac")


def test_eject_minutes_from_khac() -> None:
    from pipeline.page_audit import eject_noise_pages_from_unknown

    g = DocumentGroup(
        group_id=5,
        raw_title="khac",
        doc_type="CHUA_XAC_DINH",
        page_numbers=[32, 33, 34],
        page_size_group="OTHER",
    )
    signals = {
        32: _sig(32, header="rac", full="xxx"),
        33: _sig(
            33,
            header="BIEN BAN HAP TO CTANG",
            full="xet de nghi chuyen dang chinh thuc",
        ),
        34: _sig(34, header="de nghi", full="dong chi"),
    }
    groups, orphans, n = eject_noise_pages_from_unknown([g], signals, [])
    assert n >= 1
    assert 33 in orphans
    assert groups and 33 not in groups[0].page_numbers
    print("  OK  eject_minutes_from_khac")


def test_force_phieu_xin_y_kien_out_of_kiem_diem() -> None:
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        2014,
        None,
        5,
        None,
        "PHIEU XIN Y KIEN CHI UY",
        "Kinh gui",
    )
    assert force and "phieu_xin" in reason, (force, reason)
    print("  OK  force_phieu_xin_y_kien_out_of_kiem_diem")


def test_y_kien_does_not_swallow_kiem_diem() -> None:
    """Phiếu xin ý kiến 1 trang; trang sau BAN KIEM DIEM → NEW kiểm điểm."""
    from pipeline.doc_identity import should_force_new_document

    force, reason = should_force_new_document(
        "TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI",
        2011,
        None,
        1,
        None,
        "BAN KIEM DIEM TR PHE BINH",
        "Ha Noi, ngay 15 thang 11 nam 2012",
    )
    assert force, (force, reason)

    det = BoundaryDetector()
    det.process_page(
        _sig(
            121,
            size="A4_PORTRAIT",
            matched="TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI",
            header="PHIEU XIN Y KIEN CHI UY",
            full="Nam 2011",
        )
    )
    d122 = det.process_page(
        _sig(
            122,
            size="A4_PORTRAIT",
            header="BAN KIEM DIEM TR PHE BINH VA PHE BINH",
            full="Ha Noi ngay 15 thang 11 nam 2012",
            score=0.2,
        )
    )
    assert d122.page_class == PageClass.NEW_DOCUMENT, d122.reasoning
    for pn in (123, 124, 125, 126):
        det.process_page(
            _sig(
                pn,
                size="A4_PORTRAIT",
                header="1.2 Ban than kiem diem",
                full="noi dung kiem diem tiep",
                score=0.1,
            )
        )
    groups, orphans = det.finalize()
    yk = [g for g in groups if g.doc_type.startswith("TONG_HOP")]
    kd = [g for g in groups if g.doc_type.startswith("BAN_TU_KIEM")]
    assert yk and yk[0].page_numbers == [121], yk
    assert kd and 122 in kd[0].page_numbers
    assert all(p in kd[0].page_numbers for p in (123, 124, 125, 126)) or (
        122 in kd[0].page_numbers and not any(p in orphans for p in (122,))
    ), ([(g.doc_type, g.page_numbers) for g in groups], orphans)
    print("  OK  y_kien_does_not_swallow_kiem_diem")


def test_scrub_kiem_diem_out_of_y_kien() -> None:
    from pipeline.page_audit import scrub_mismatched_form_pages

    g = DocumentGroup(
        group_id=1,
        raw_title="yk",
        doc_type="TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI",
        page_numbers=[121, 122, 123],
        page_size_group="A4_PORTRAIT",
    )
    signals = {
        121: _sig(121, header="PHIEU XIN Y KIEN", full="a", size="A4_PORTRAIT"),
        122: _sig(
            122,
            header="BAN KIEM DIEM TR PHE BINH",
            full="nam 2012",
            size="A4_PORTRAIT",
        ),
        123: _sig(123, header="1.2 Ban than", full="kiem diem", size="A4_PORTRAIT"),
    }
    groups, n = scrub_mismatched_form_pages([g], signals)
    assert n >= 1
    yk = next(g for g in groups if g.doc_type.startswith("TONG_HOP"))
    assert yk.page_numbers == [121]
    kd = next(g for g in groups if g.doc_type.startswith("BAN_TU_KIEM"))
    assert 122 in kd.page_numbers and 123 in kd.page_numbers
    print("  OK  scrub_kiem_diem_out_of_y_kien")


def test_year_ignores_sink_1gay_dob() -> None:
    from pipeline.year_aware_sequencer import extract_year_robust

    y = extract_year_robust(
        "BAN TU KIEN AIEN DANG VIEN\nSINK 1GAY : 26/03/1966\nNgay vao dang: 04/10/1993"
    )
    assert y != 1966, y
    assert y in (1993, None) or (y is not None and y >= 1990), y
    print("  OK  year_ignores_sink_1gay_dob")


def main() -> int:
    logger.remove()
    print("=" * 60)
    print("  Sprint improvements — smoke tests")
    print("=" * 60)
    tests = [
        test_classify_page_size,
        test_soft_booklet_continuation,
        test_size_change_is_new,
        test_reattach_sandwich,
        test_reattach_sandwich_not_into_quyet_dinh_minutes,
        test_quyet_dinh_not_swallow_bien_ban,
        test_reattach_case4_size,
        test_reattach_trailing_phieu_dang_vien_page,
        test_eod_signature_label,
        test_alias_size_gate,
        test_manifest_toc,
        test_toc_never_into_ly_lich,
        test_booklet_to_a4_hard_boundary,
        test_page_audit_complete,
        test_so_ly_lich_not_on_a4_phieu,
        test_bien_ban_not_kiem_diem,
        test_force_phieu_and_quyet_dinh_split,
        test_manifest_without_co_khong,
        test_prev_eod_boost,
        test_identity_extract_from_phieu_and_cli_override,
        test_identity_reject_tdv_and_garbage_name,
        test_soft_max_ly_lich_and_toc_closes_kiem_diem,
        test_refine_unknown_group_types,
        test_orphan_closes_phieu_dang_vien,
        test_qd_tu_alias_and_chuan_y,
        test_force_phieu_bo_sung_year_and_kiem_diem,
        test_phieu_numbered_fields_not_form_section,
        test_form_section_continues_into_phieu,
        test_multipass_sandwich_reattach,
        test_year_from_thang_nam_and_ocr_blob,
        test_orphan_review_namer,
        test_reattach_respects_soft_max,
        test_promote_orphans_to_groups,
        test_identity_dot_ocr_name,
        test_identity_pham_huu_luat_and_tdv_fallback,
        test_absorb_trailing_after_soft_max,
        test_reattach_phieu_not_across_toc_into_ly_lich,
        test_scrub_phieu_out_of_ly_lich,
        test_merge_adjacent_same_year_phieu,
        test_soft_max_does_not_copy_phieu_onto_kiem_diem,
        test_year_ignores_ngay_sinh,
        test_force_phieu_to_kiem_diem_without_catalog,
        test_kiem_diem_does_not_swallow_ke_khai_tai_san,
        test_scrub_ke_khai_out_of_kiem_diem,
        test_jammed_ocr_kiem_diem_and_refine_khac,
        test_eject_minutes_from_khac,
        test_force_phieu_xin_y_kien_out_of_kiem_diem,
        test_y_kien_does_not_swallow_kiem_diem,
        test_scrub_kiem_diem_out_of_y_kien,
        test_year_ignores_sink_1gay_dob,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
    print("=" * 60)
    if failed:
        print(f"  {failed}/{len(tests)} FAILED")
        return 1
    print(f"  ALL {len(tests)} PASSED")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
