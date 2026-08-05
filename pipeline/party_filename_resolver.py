"""
pipeline/party_filename_resolver.py
====================================
Giải quyết tên tệp PDF đầu ra cho từng tài liệu trong hồ sơ đảng viên
theo Phụ lục 2 — Hướng dẫn 1361-CV/BTCTW.

Hai quy tắc cốt lõi:

1. Zero-padding cho STT (edge case 4):
   • STT  1– 99 -> 2 chữ số  (01, 09, 10, 99)
   • STT 100–104-> 3 chữ số  (100, 101, 104)

2. Đuôi số thứ tự khi xuất hiện nhiều lần (edge case 3):
   • n = 1 (chỉ 1 tài liệu của loại này): KHÔNG thêm ".1"
     -> "02.Ly lich dang vien.pdf"
   • n >= 2 (nhiều tài liệu cùng loại): BẮT BUỘC thêm ".[SoThuTu]"
     -> "55.Giay gioi thieu sinh hoat dang tam thoi.1.pdf"
     -> "55.Giay gioi thieu sinh hoat dang tam thoi.2.pdf"

Khoảng trắng trong tên tệp (edge case 2 — phía file):
   Mặc định GIỮ khoảng trắng theo đúng ví dụ Phụ lục 2.
   Dùng STRIP_FILE_SPACES=True để viết liền (cho nghiệm thu đặc biệt).

Workflow điển hình:
    resolver = PartyFilenameResolver()

    # Đăng ký toàn bộ tài liệu đã nhận dạng trong một hồ sơ:
    for doc_type_key in detected_docs:
        resolver.register(doc_type_key)

    # Sau khi đăng ký xong, lấy tên file theo thứ tự xuất hiện:
    resolver.reset_counters()
    for doc_type_key in detected_docs:
        filename = resolver.next_filename(doc_type_key)
        print(filename)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from pipeline.party_catalog import PARTY_DOC_CATALOG, fmt_stt


# ── Config flag (edge case 2 — tên tệp) ──────────────────────────────────────
#
# False (mặc định): giữ khoảng trắng, VD "01.Ly lich nguoi xin vao dang.pdf"
# True             : viết liền,         VD "01.Lylichnguoixinvaodang.pdf"
STRIP_FILE_SPACES: bool = False


# ── Dataclass mô tả một tài liệu đã được giải quyết tên ─────────────────────

@dataclass
class ResolvedFilename:
    """Kết quả giải quyết tên tệp cho một tài liệu."""
    doc_type_key: str
    stt_formatted: str          # VD "01", "55", "100"
    ten_tai_lieu: str           # Tên tài liệu không dấu (đã xử lý STRIP_FILE_SPACES)
    occurrence_index: int       # Số thứ tự lần xuất hiện (1-based)
    total_occurrences: int      # Tổng số lần loại này xuất hiện trong hồ sơ
    filename: str               # Tên tệp đầy đủ kể cả đuôi .pdf

    @property
    def is_duplicate(self) -> bool:
        """Trả về True nếu loại tài liệu này xuất hiện >= 2 lần."""
        return self.total_occurrences >= 2


# ── Lớp chính ─────────────────────────────────────────────────────────────────

class PartyFilenameResolver:
    """
    Bộ giải quyết tên tệp cho toàn bộ tài liệu trong một hồ sơ đảng viên.

    Vòng đời:
        1. register(doc_type_key)  — gọi cho từng tài liệu theo thứ tự xuất hiện.
        2. reset_counters()        — reset bộ đếm về đầu hàng (giữ nguyên total_counts).
        3. next_filename(key)      — gọi theo cùng thứ tự như bước 1 để lấy tên tệp.

    Hoặc dùng resolve_all() để xử lý toàn bộ danh sách một lần.
    """

    def __init__(self, strip_spaces: bool = STRIP_FILE_SPACES) -> None:
        self._strip_spaces: bool = strip_spaces

        # Số lần xuất hiện của mỗi doc_type_key trong HỒ SƠ (sau khi register xong)
        self._total_counts: dict[str, int] = defaultdict(int)

        # Bộ đếm rolling: số lần next_filename() đã được gọi cho từng key
        self._current_counters: dict[str, int] = defaultdict(int)

        # Danh sách thứ tự đăng ký (để kiểm tra nhất quán)
        self._registered_order: list[str] = []

        logger.debug(
            "[filename_resolver] Khởi tạo (strip_spaces={})", strip_spaces
        )

    # ── Bước 1: Đăng ký ──────────────────────────────────────────────────────

    def register(self, doc_type_key: str) -> None:
        """
        Đăng ký sự xuất hiện của một loại tài liệu.
        Phải gọi cho TOÀN BỘ tài liệu trước khi bắt đầu next_filename().

        Args:
            doc_type_key: Mã nội bộ tài liệu (VD "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI").

        Raises:
            KeyError: Nếu key không tồn tại trong PARTY_DOC_CATALOG.
        """
        key = doc_type_key.upper()
        if key not in PARTY_DOC_CATALOG:
            logger.error(
                "[filename_resolver] Key không tồn tại trong catalog: {!r}", key
            )
            raise KeyError(
                f"doc_type_key {key!r} không có trong PARTY_DOC_CATALOG"
            )
        self._total_counts[key] += 1
        self._registered_order.append(key)
        logger.debug(
            "[filename_resolver] Đã đăng ký {!r} (lần thứ {})",
            key, self._total_counts[key],
        )

    def register_many(self, keys: list[str]) -> None:
        """Đăng ký nhiều tài liệu một lúc (giữ nguyên thứ tự)."""
        for key in keys:
            self.register(key)

    # ── Bước 2: Reset bộ đếm rolling ─────────────────────────────────────────

    def reset_counters(self) -> None:
        """
        Reset bộ đếm rolling về 0 để bắt đầu vòng next_filename() từ đầu.
        _total_counts được giữ nguyên.
        """
        self._current_counters = defaultdict(int)
        logger.debug("[filename_resolver] reset_counters() — bộ đếm đã reset.")

    # ── Bước 3: Lấy tên tệp ──────────────────────────────────────────────────

    def next_filename(self, doc_type_key: str) -> ResolvedFilename:
        """
        Trả về tên tệp cho lần xuất hiện tiếp theo của doc_type_key.

        Edge case 3:
          • total = 1 -> "XX.Ten tai lieu.pdf"           (KHÔNG có ".1")
          • total >= 2-> "XX.Ten tai lieu.N.pdf"          (N = 1, 2, 3, …)

        Edge case 2 (file):
          • STRIP_FILE_SPACES=False -> giữ khoảng trắng
          • STRIP_FILE_SPACES=True  -> loại bỏ khoảng trắng

        Edge case 4:
          • STT 1–99   -> 2 chữ số
          • STT 100–104-> 3 chữ số

        Raises:
            KeyError:       Key chưa được register.
            RuntimeError:   Đã vượt quá số lần đã đăng ký.
        """
        key = doc_type_key.upper()
        if key not in self._total_counts:
            logger.error(
                "[filename_resolver] Key {!r} chưa được register(). "
                "Hãy gọi register() trước.",
                key,
            )
            raise KeyError(f"{key!r} chưa được đăng ký. Gọi register() trước.")

        total = self._total_counts[key]
        self._current_counters[key] += 1
        idx = self._current_counters[key]

        if idx > total:
            raise RuntimeError(
                f"next_filename() được gọi quá {total} lần cho {key!r}. "
                f"Đã gọi lần thứ {idx}."
            )

        stt_raw, ten_raw, _ = PARTY_DOC_CATALOG[key]
        stt = fmt_stt(stt_raw)

        # Xử lý khoảng trắng trong tên tệp (edge case 2 — phía file)
        ten = ten_raw.replace(" ", "") if self._strip_spaces else ten_raw

        # Xây dựng tên tệp (edge case 3)
        if total == 1:
            # n = 1: KHÔNG thêm ".1"
            filename = f"{stt}.{ten}.pdf"
        else:
            # n >= 2: BẮT BUỘC thêm ".[SoThuTu]"
            filename = f"{stt}.{ten}.{idx}.pdf"

        logger.debug(
            "[filename_resolver] {} -> {!r} (occurrences {}/{})",
            key, filename, idx, total,
        )

        return ResolvedFilename(
            doc_type_key=key,
            stt_formatted=stt,
            ten_tai_lieu=ten,
            occurrence_index=idx,
            total_occurrences=total,
            filename=filename,
        )

    # ── Hàm tổng hợp một lần ─────────────────────────────────────────────────

    def resolve_all(
        self, doc_type_keys: list[str]
    ) -> list[ResolvedFilename]:
        """
        Convenience method: đăng ký + resolve toàn bộ danh sách một lần.

        Tương đương với:
            resolver.register_many(keys)
            resolver.reset_counters()
            return [resolver.next_filename(k) for k in keys]

        Lưu ý: Reset toàn bộ trạng thái trước khi xử lý.

        Args:
            doc_type_keys: Danh sách key theo đúng thứ tự xuất hiện.

        Returns:
            List[ResolvedFilename] theo cùng thứ tự.
        """
        # Reset hoàn toàn
        self._total_counts = defaultdict(int)
        self._current_counters = defaultdict(int)
        self._registered_order = []

        self.register_many(doc_type_keys)
        self.reset_counters()

        results: list[ResolvedFilename] = []
        for key in doc_type_keys:
            try:
                resolved = self.next_filename(key)
                results.append(resolved)
            except (KeyError, RuntimeError) as exc:
                logger.error(
                    "[filename_resolver] Lỗi resolve {!r}: {}", key, exc
                )
                raise

        logger.info(
            "[filename_resolver] resolve_all() xong: {} tài liệu, "
            "{} loại phân biệt.",
            len(results),
            len(self._total_counts),
        )
        return results

    # ── Truy vấn trạng thái ──────────────────────────────────────────────────

    @property
    def total_counts(self) -> dict[str, int]:
        """Bản sao bộ đếm tổng (không sửa được trực tiếp)."""
        return dict(self._total_counts)

    def duplicates(self) -> dict[str, int]:
        """
        Trả về dict các loại tài liệu xuất hiện từ 2 lần trở lên.
        {doc_type_key: count}
        """
        return {k: v for k, v in self._total_counts.items() if v >= 2}

    def reset_all(self) -> None:
        """Reset toàn bộ trạng thái (kể cả total_counts)."""
        self._total_counts = defaultdict(int)
        self._current_counters = defaultdict(int)
        self._registered_order = []
        logger.debug("[filename_resolver] reset_all() — toàn bộ trạng thái đã xóa.")


# ── Hàm tiện ích standalone (không cần instance) ─────────────────────────────

def build_filename(
    doc_type_key: str,
    occurrence_index: int,
    total_occurrences: int,
    strip_spaces: bool = STRIP_FILE_SPACES,
) -> str:
    """
    Xây dựng tên tệp cho một tài liệu đơn lẻ khi đã biết trước
    occurrence_index và total_occurrences.

    Tiện dụng khi không cần quản lý toàn bộ hồ sơ.

    Args:
        doc_type_key      : Mã nội bộ tài liệu.
        occurrence_index  : Thứ tự xuất hiện (1-based).
        total_occurrences : Tổng số lần xuất hiện trong hồ sơ.
        strip_spaces      : Xóa khoảng trắng trong tên tài liệu hay không.

    Returns:
        Tên tệp PDF hoàn chỉnh.

    Raises:
        KeyError: Nếu key không có trong catalog.
        ValueError: Nếu occurrence_index > total_occurrences.
    """
    key = doc_type_key.upper()
    if key not in PARTY_DOC_CATALOG:
        raise KeyError(f"doc_type_key {key!r} không có trong PARTY_DOC_CATALOG")
    if occurrence_index < 1 or occurrence_index > total_occurrences:
        raise ValueError(
            f"occurrence_index={occurrence_index} không hợp lệ "
            f"(total_occurrences={total_occurrences})"
        )

    stt_raw, ten_raw, _ = PARTY_DOC_CATALOG[key]
    stt = fmt_stt(stt_raw)
    ten = ten_raw.replace(" ", "") if strip_spaces else ten_raw

    if total_occurrences == 1:
        return f"{stt}.{ten}.pdf"
    return f"{stt}.{ten}.{occurrence_index}.pdf"


# ── Smoke-test CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True)

    # Giả lập một hồ sơ đảng viên điển hình
    sample_docs = [
        "LY_LICH_NGUOI_XIN_VAO_DANG",           # STT 01, n=1
        "LY_LICH_DANG_VIEN",                    # STT 02, n=1
        "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI",  # STT 55, n=1 lần đầu
        "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI",  # STT 55, n=2 lần sau
        "BAN_TU_KIEM_DIEM_HANG_NAM",            # STT 61, n=1 lần đầu
        "BAN_TU_KIEM_DIEM_HANG_NAM",            # STT 61, n=2
        "BAN_TU_KIEM_DIEM_HANG_NAM",            # STT 61, n=3
        "PHIEU_BAO_CONG_NHAN_CHINH_THUC",       # STT 100, n=1
        "TO_KHAI_DE_NGHI_TRUY_TANG_HUY_HIEU_DANG",  # STT 104, n=1
    ]

    resolver = PartyFilenameResolver(strip_spaces=False)
    results = resolver.resolve_all(sample_docs)

    print(f"\n{'=' * 72}")
    print("  party_filename_resolver — smoke test (STRIP_FILE_SPACES=False)")
    print(f"{'=' * 72}")
    for r in results:
        dup_flag = f"[n={r.total_occurrences}]" if r.is_duplicate else "[n=1]"
        print(f"  {dup_flag:<7} {r.filename}")

    print(f"\n--- Kiểm tra STRIP_FILE_SPACES=True ---")
    resolver2 = PartyFilenameResolver(strip_spaces=True)
    results2 = resolver2.resolve_all(sample_docs)
    for r in results2:
        print(f"  {r.filename}")

    print(f"\n--- build_filename() standalone ---")
    print(build_filename("GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI", 1, 2))
    print(build_filename("GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI", 2, 2))
    print(build_filename("LY_LICH_NGUOI_XIN_VAO_DANG", 1, 1))
    print(build_filename("PHIEU_BAO_CONG_NHAN_CHINH_THUC", 1, 1))
