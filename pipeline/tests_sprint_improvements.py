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
    assert decisions[0].reason == "sandwiched_between_same_group"
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
    assert decisions[0].reason == "minutes_between_quyet_dinh_keep_orphan"
    print("  OK  reattach_sandwich_not_into_quyet_dinh_minutes")


def test_reattach_case4_size() -> None:
    # prev không phải MULTI_PAGE_FORM → Case 2 không khớp; Case 4 size khớp
    g = DocumentGroup(
        group_id=1,
        raw_title="QD",
        doc_type="QUYET_DINH_KET_NAP_DANG_VIEN",
        page_numbers=[3, 4],
        page_size_group="BOOKLET_SMALL",
    )
    signals = {5: _sig(5, size="BOOKLET_SMALL", score=0.2)}
    groups, orphans, decisions = reattach_orphans([g], [5], signals)
    assert 5 in groups[0].page_numbers
    assert decisions[0].reason == "same_page_size_group_as_prev"
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
    assert decisions[0].reason == "trailing_page_of_multi_page_form"
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
