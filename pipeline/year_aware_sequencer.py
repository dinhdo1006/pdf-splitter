"""
pipeline/year_aware_sequencer.py
=================================
Trích xuất năm từ văn bản OCR và gán số thứ tự cho các tài liệu cùng loại
theo thứ tự năm cũ → mới (year-aware ordering).

Modules được sử dụng bởi main.py sau khi detector.finalize() để:
  1. Tìm doc_year từ raw_title nếu LLM chưa điền được
  2. Sort các tài liệu cùng doc_type_key theo năm
  3. Gán sequence_number = 1, 2, 3... (0 = chỉ có 1 tài liệu, không cần số)
  4. Kết quả được ghi vào group._sequence_number để FilenameResolver dùng

Áp dụng config:
  YEAR_EXTRACT_OCR_FIXUP  : sửa OCR nhầm O→0, l→1, I→1
  YEAR_MIN_VALID          : năm tối thiểu hợp lệ
  YEAR_MAX_VALID          : năm tối đa hợp lệ
  YEAR_SENTINEL_NO_YEAR   : sentinel cho tài liệu không có năm (xếp cuối)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

import config


# ── Hằng số nội bộ ───────────────────────────────────────────────────────────

_YEAR_MIN: int = getattr(config, "YEAR_MIN_VALID",        1945)
_YEAR_MAX: int = getattr(config, "YEAR_MAX_VALID",        2035)
_SENTINEL: int = getattr(config, "YEAR_SENTINEL_NO_YEAR", 9999)
_OCR_FIXUP: bool = getattr(config, "YEAR_EXTRACT_OCR_FIXUP", True)

# Map ký tự OCR nhầm → số đúng (chỉ áp dụng cho phần tìm năm)
_OCR_CHAR_MAP: dict[str, str] = {"O": "0", "l": "1", "I": "1"}


# ── Dataclass DocRecord ───────────────────────────────────────────────────────

@dataclass
class DocRecord:
    """
    Ánh xạ một DocumentGroup sang một bản ghi có thể sắp xếp theo năm.

    Attributes:
        doc_type_key:    Mã loại tài liệu (VD "BAN_TU_KIEM_DIEM_HANG_NAM").
        raw_title:       Tiêu đề thô từ OCR (dùng để extract năm nếu doc_year=None).
        page_numbers:    Danh sách số trang (1-based).
        pdf_order:       Thứ tự xuất hiện trong PDF (0-based, dùng làm fallback sort).
        doc_year:        Năm ban hành (None nếu chưa biết).
        doc_month:       Tháng ban hành (None nếu chưa biết).
        doc_day:         Ngày ban hành (None nếu chưa biết).
        sequence_number: Số thứ tự trong nhóm cùng loại.
                         0 = chưa gán hoặc nhóm chỉ có 1 tài liệu (không cần số).
        ocr_blob:        OCR đầy đủ hơn (header+full các trang) để bổ sung năm.
    """
    doc_type_key:    str
    raw_title:       str
    page_numbers:    list[int]
    pdf_order:       int
    doc_year:        Optional[int] = None
    doc_month:       Optional[int] = None
    doc_day:         Optional[int] = None
    sequence_number: int           = 0
    ocr_blob:        str           = ""


# ── Năm / ngày extraction ─────────────────────────────────────────────────────

def _apply_ocr_fixup(text: str) -> str:
    """
    Thay thế các ký tự OCR thường bị nhầm trong chuỗi số năm:
      O → 0,  l → 1,  I → 1

    Chỉ áp dụng khi config.YEAR_EXTRACT_OCR_FIXUP = True.
    Trả về text gốc nếu _OCR_FIXUP = False.
    """
    if not _OCR_FIXUP:
        return text
    result = []
    for ch in text:
        result.append(_OCR_CHAR_MAP.get(ch, ch))
    return "".join(result)


def _is_valid_year(year: int) -> bool:
    """Kiểm tra năm nằm trong khoảng [YEAR_MIN_VALID, YEAR_MAX_VALID]."""
    return _YEAR_MIN <= year <= _YEAR_MAX


def extract_year_robust(text: str) -> Optional[int]:
    """
    Trích xuất năm ban hành từ văn bản OCR theo 3 pattern ưu tiên giảm dần.

    Pattern ưu tiên:
      1. r"n[aă]m\\s+(\\d{4})"           — "năm 2024" / "nam 2024" (OCR mất dấu)
      2. r"ngày\\s+\\d+[/\\-\\.]\\d+[/\\-\\.](\\d{4})"  — ngày tháng năm đầy đủ
      3. r"\\b(\\d{4})\\b"                — năm đứng riêng (ưu tiên thấp nhất)

    Nếu config.YEAR_EXTRACT_OCR_FIXUP=True: áp dụng OCR char fixup trước khi match.

    Args:
        text: Văn bản OCR thô (có thể có dấu tiếng Việt hoặc không).

    Returns:
        Số nguyên năm hợp lệ trong [YEAR_MIN_VALID, YEAR_MAX_VALID],
        hoặc None nếu không tìm thấy / ngoài khoảng.
    """
    if not text or not text.strip():
        return None

    fixed = _apply_ocr_fixup(text)

    # Pattern 1 — "năm 2024" / "nam 2024"
    for m in re.finditer(r"n[aă]m\s+(\d{4})", fixed, re.IGNORECASE):
        try:
            y = int(m.group(1))
            if _is_valid_year(y):
                return y
        except (ValueError, IndexError):
            continue

    # Pattern 2 — "ngày DD/MM/YYYY" hoặc DD-MM-YYYY / DD.MM.YYYY
    for m in re.finditer(
        r"ng[àa]y\s+\d{1,2}[/\-\.]\d{1,2}[/\-\.](\d{4})", fixed, re.IGNORECASE
    ):
        try:
            y = int(m.group(1))
            if _is_valid_year(y):
                return y
        except (ValueError, IndexError):
            continue

    # Pattern 2b — "ngày 06 tháng 01 năm 1995" / "thang 01 nam 1995"
    for m in re.finditer(
        r"(?:ng[àa]y\s+\d{1,2}\s+)?th[aá]ng\s+\d{1,2}\s+n[aă]m\s+(\d{4})",
        fixed,
        re.IGNORECASE,
    ):
        try:
            y = int(m.group(1))
            if _is_valid_year(y):
                return y
        except (ValueError, IndexError):
            continue

    # Pattern 2c — bare DD/MM/YYYY
    for m in re.finditer(r"\b\d{1,2}[/\-\.]\d{1,2}[/\-\.](\d{4})\b", fixed):
        try:
            y = int(m.group(1))
            if _is_valid_year(y):
                return y
        except (ValueError, IndexError):
            continue

    # Pattern 3 — số 4 chữ số đứng riêng (ưu tiên thấp nhất — lấy cái đầu tiên hợp lệ)
    for m in re.finditer(r"\b(\d{4})\b", fixed):
        try:
            y = int(m.group(1))
            if _is_valid_year(y):
                return y
        except (ValueError, IndexError):
            continue

    return None


def extract_date_components(
    text: str,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Trích xuất (year, month, day) từ cụm ngày tháng năm đầy đủ trong văn bản OCR.

    Pattern: r"ngày\\s+(\\d{1,2})[/\\-\\.](\\d{1,2})[/\\-\\.](\\d{4})"

    Validate:
      month ∈ [1, 12], day ∈ [1, 31], year ∈ [YEAR_MIN_VALID, YEAR_MAX_VALID]

    Args:
        text: Văn bản OCR thô.

    Returns:
        Tuple (year, month, day) — mỗi phần tử có thể là None nếu không tìm thấy
        hoặc nằm ngoài khoảng hợp lệ.
    """
    if not text or not text.strip():
        return None, None, None

    fixed = _apply_ocr_fixup(text)

    patterns = [
        re.compile(
            r"ng[àa]y\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"ng[àa]y\s+(\d{1,2})\s+th[aá]ng\s+(\d{1,2})\s+n[aă]m\s+(\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b",
        ),
    ]
    for pattern in patterns:
        for m in pattern.finditer(fixed):
            try:
                day = int(m.group(1))
                month = int(m.group(2))
                year = int(m.group(3))

                day_ok = 1 <= day <= 31
                month_ok = 1 <= month <= 12
                year_ok = _is_valid_year(year)

                return (
                    year if year_ok else None,
                    month if month_ok else None,
                    day if day_ok else None,
                )
            except (ValueError, IndexError):
                continue

    return None, None, None


# ── YearAwareSequencer ────────────────────────────────────────────────────────

class YearAwareSequencer:
    """
    Gán số thứ tự cho tài liệu cùng loại theo thứ tự năm cũ → mới.

    Workflow:
      1. assign_sequence(records) — nhóm theo doc_type_key, điền năm còn thiếu,
         sort theo (year, month, day, pdf_order), gán sequence_number.
      2. build_year_summary(records) — trả về dict thống kê.
    """

    def assign_sequence(self, records: list[DocRecord]) -> list[DocRecord]:
        """
        Nhóm records theo doc_type_key, sort theo năm rồi gán sequence_number.

        Quy tắc:
          • len == 1 → sequence_number = 0 (không cần số)
          • len >= 2 → sequence_number = 1, 2, 3... (theo năm cũ → mới)

        Sort key: (doc_year is None, doc_year or SENTINEL, doc_month or 13,
                   doc_day or 32, pdf_order)
          — tài liệu không có năm luôn xếp CUỐI nhóm.

        Với mỗi record thiếu doc_year: thử extract từ raw_title trước khi sort.

        Args:
            records: Danh sách DocRecord (thứ tự bất kỳ, có thể mutate in-place).

        Returns:
            Cùng list records đã được gán sequence_number (mutate in-place).
        """
        # Nhóm theo doc_type_key
        groups: dict[str, list[DocRecord]] = defaultdict(list)
        for rec in records:
            groups[rec.doc_type_key].append(rec)

        for key, grp in groups.items():
            # 1. Điền năm/tháng/ngày còn thiếu từ raw_title + ocr_blob
            for rec in grp:
                blob = "\n".join(
                    x for x in (rec.raw_title or "", rec.ocr_blob or "") if x
                )
                if rec.doc_year is None:
                    extracted_year = extract_year_robust(blob)
                    if extracted_year is not None:
                        rec.doc_year = extracted_year
                        logger.debug(
                            "[sequencer] Điền doc_year={} từ OCR blob "
                            "cho pdf_order={} (key={!r})",
                            extracted_year, rec.pdf_order, key,
                        )
                y, mo, d = extract_date_components(blob)
                if rec.doc_year is None and y is not None:
                    rec.doc_year = y
                if rec.doc_month is None and mo is not None:
                    rec.doc_month = mo
                if rec.doc_day is None and d is not None:
                    rec.doc_day = d

            # 2. Log warning cho record vẫn không có năm
            no_year_count = sum(1 for r in grp if r.doc_year is None)
            if no_year_count > 0:
                logger.warning(
                    "[sequencer] {}/{} bản ghi loại {!r} không có doc_year — "
                    "xếp cuối nhóm (sentinel={})",
                    no_year_count, len(grp), key, _SENTINEL,
                )

            # 3. Gán sequence_number
            if len(grp) == 1:
                grp[0].sequence_number = 0
                continue

            # Sort theo năm/tháng/ngày → pdf_order
            try:
                grp.sort(
                    key=lambda r: (
                        r.doc_year is None,          # True (1) → cuối
                        r.doc_year  or _SENTINEL,
                        r.doc_month or 13,
                        r.doc_day   or 32,
                        r.pdf_order,
                    )
                )
            except Exception as exc:
                logger.error(
                    "[sequencer] Lỗi sort nhóm {!r}: {} — fallback về pdf_order",
                    key, exc,
                )
                try:
                    grp.sort(key=lambda r: r.pdf_order)
                except Exception as exc2:
                    logger.error(
                        "[sequencer] Fallback sort cũng lỗi nhóm {!r}: {}", key, exc2
                    )

            for idx, rec in enumerate(grp, start=1):
                rec.sequence_number = idx
                logger.debug(
                    "[sequencer] {!r} pdf_order={} year={} → sequence_number={}",
                    key, rec.pdf_order, rec.doc_year, idx,
                )

        logger.info(
            "[sequencer] assign_sequence() hoàn tất: {} records, {} loại phân biệt.",
            len(records), len(groups),
        )
        return records

    def build_year_summary(self, records: list[DocRecord]) -> dict:
        """
        Xây dựng dict thống kê năm cho toàn bộ hồ sơ.

        Args:
            records: Danh sách DocRecord sau khi assign_sequence().

        Returns:
            {
              "total":        N,    — tổng số tài liệu
              "with_year":    M,    — số tài liệu có doc_year
              "without_year": K,    — số tài liệu không có doc_year
              "doc_types": {        — thống kê theo từng loại
                  "<key>": {
                      "count":         int,
                      "with_year":     int,
                      "years":         list[int],   — danh sách năm (unique, sorted)
                  }
              }
            }
        """
        total       = len(records)
        with_year   = sum(1 for r in records if r.doc_year is not None)
        without_year = total - with_year

        doc_types: dict[str, dict] = {}
        for rec in records:
            entry = doc_types.setdefault(
                rec.doc_type_key,
                {"count": 0, "with_year": 0, "years": set()},
            )
            entry["count"] += 1
            if rec.doc_year is not None:
                entry["with_year"] += 1
                entry["years"].add(rec.doc_year)

        # Chuyển set → list sorted để serialize được JSON
        for entry in doc_types.values():
            entry["years"] = sorted(entry["years"])

        return {
            "total":        total,
            "with_year":    with_year,
            "without_year": without_year,
            "doc_types":    doc_types,
        }


# ── Smoke-test CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True)

    # ── Test extract_year_robust ──────────────────────────────────────
    print("=" * 64)
    print("  extract_year_robust — smoke test")
    print("=" * 64)
    test_texts = [
        ("năm 2024",                     2024),
        ("nam 2O24",                     2024),   # OCR: O → 0
        ("ngày 15/03/2019 họp chi bộ",  2019),
        ("ngày 1-7-l975 quyết định",    1975),   # l → 1
        ("QUYẾT ĐỊNH SỐ 123/QĐ-ĐU",    None),   # không có năm
        ("BÁO CÁO 2022 - TỔNG KẾT",    2022),
        ("năm 1944",                     None),   # < YEAR_MIN_VALID
        ("năm 2036",                     None),   # > YEAR_MAX_VALID
        ("",                             None),
    ]
    all_ok = True
    for text, expected in test_texts:
        result = extract_year_robust(text)
        ok = result == expected
        status = "OK" if ok else f"FAIL (got {result!r})"
        print(f"  {status:<8}  {text!r:<42} -> {result!r}")
        if not ok:
            all_ok = False
    print(f"\n  {'ALL PASS' if all_ok else 'SOME FAILURES'}")

    # ── Test extract_date_components ──────────────────────────────────
    print("\n" + "=" * 64)
    print("  extract_date_components — smoke test")
    print("=" * 64)
    date_tests = [
        ("ngày 15/03/2019",             (2019, 3, 15)),
        ("ngày 1-7-1975",               (1975, 7, 1)),
        ("Ngày 31.12.2025 ban hành",    (2025, 12, 31)),
        ("không có ngày tháng",         (None, None, None)),
        ("ngày 00/13/2020",             (2020, None, None)),  # day=0 invalid, month=13 invalid
    ]
    for text, expected in date_tests:
        result = extract_date_components(text)
        ok = result == expected
        status = "OK" if ok else f"FAIL (got {result!r})"
        print(f"  {status:<8}  {text!r:<38} -> {result!r}")

    # ── Test YearAwareSequencer ───────────────────────────────────────
    print("\n" + "=" * 64)
    print("  YearAwareSequencer.assign_sequence() — smoke test")
    print("=" * 64)
    records = [
        DocRecord("BAN_TU_KIEM_DIEM_HANG_NAM", "Ban tu kiem diem nam 2023",   [10, 11], 0, doc_year=2023),
        DocRecord("BAN_TU_KIEM_DIEM_HANG_NAM", "Ban tu kiem diem năm 2021",   [5, 6],   1, doc_year=None),
        DocRecord("BAN_TU_KIEM_DIEM_HANG_NAM", "Ban tu kiem diem nam 2025",   [20],     2, doc_year=2025),
        DocRecord("LY_LICH_DANG_VIEN",         "Ly lich dang vien",           [1, 2, 3],3, doc_year=2018),
        DocRecord("GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI", "Giay gioi thieu nam 2020", [7], 4, doc_year=None),
        DocRecord("GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI", "Giay gioi thieu 2022",     [12],5, doc_year=2022),
        DocRecord("QUYET_DINH_KY_LUAT_DANG",   "Quyet dinh ky luat",         [15],     6, doc_year=None),
    ]

    seq = YearAwareSequencer()
    result_records = seq.assign_sequence(records)

    print("\n  Kết quả sau assign_sequence:")
    for r in result_records:
        print(
            f"  [{r.doc_type_key:<40}] "
            f"pdf_order={r.pdf_order} year={str(r.doc_year):<6} "
            f"seq_num={r.sequence_number}"
        )

    summary = seq.build_year_summary(result_records)
    print(f"\n  Summary JSON:\n{json.dumps(summary, ensure_ascii=False, indent=2)}")

    # Kiểm tra thứ tự đúng cho BAN_TU_KIEM_DIEM_HANG_NAM
    kiem_diem = sorted(
        [r for r in result_records if r.doc_type_key == "BAN_TU_KIEM_DIEM_HANG_NAM"],
        key=lambda r: r.sequence_number,
    )
    assert kiem_diem[0].sequence_number == 1 and kiem_diem[0].doc_year == 2021, \
        f"Expected seq=1 for year=2021 (oldest), got {kiem_diem[0]}"

    print("\n  BAN_TU_KIEM_DIEM_HANG_NAM sort order:")
    for r in kiem_diem:
        print(f"    seq={r.sequence_number}  year={r.doc_year}  pdf_order={r.pdf_order}")
    print("\n  PASS — YearAwareSequencer smoke test OK")
