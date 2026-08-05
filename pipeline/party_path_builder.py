"""
pipeline/party_path_builder.py
==============================
Xây dựng đường dẫn thư mục chuẩn hóa cho hồ sơ đảng viên
theo Phụ lục 2 — Hướng dẫn 1361-CV/BTCTW.

Cấu trúc thư mục:
    <base_output_dir>/
      [M1].[M2].[M3].[M4].[M5]/
        [SoCCCD]_[HoTenPascalCase]/

Quy tắc zero-padding mã cấp ủy (edge case 1):
  • M1 (Cấp TW): luôn 2 chữ số         (VD: "93" -> "93", "3" -> "03")
  • M2, M3, M4, M5: luôn 3 chữ số      (VD: "15" -> "015", "0" -> "000")

Quy tắc tên thư mục (edge case 2):
  • HoTenDangVien: bỏ dấu tiếng Việt, viết liền KHÔNG khoảng trắng (PascalCase).
    VD: "Nguyễn Văn A" -> "NguyenVanA"
  • Folder name: "[SoCCCD]_[HoTenPascalCase]"
    VD: "012345678901_NguyenVanA"
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from loguru import logger
from unidecode import unidecode


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_m1(code: str | int) -> str:
    """
    Format mã M1 (Cấp TW): 2 chữ số, không có dấu chấm đầu.

    >>> _fmt_m1("93")  -> "93"
    >>> _fmt_m1(3)     -> "03"
    >>> _fmt_m1("0")   -> "00"
    """
    n = int(str(code).strip())
    if n < 0 or n > 99:
        raise ValueError(f"M1 phải trong khoảng 0–99, nhận được: {code!r}")
    return f"{n:02d}"


def _fmt_mx(code: str | int) -> str:
    """
    Format mã M2–M5: 3 chữ số.

    >>> _fmt_mx("15")  -> "015"
    >>> _fmt_mx(0)     -> "000"
    >>> _fmt_mx("999") -> "999"
    """
    n = int(str(code).strip())
    if n < 0 or n > 999:
        raise ValueError(f"Mã M2–M5 phải trong khoảng 0–999, nhận được: {code!r}")
    return f"{n:03d}"


def _to_pascal_no_space(name: str) -> str:
    """
    Chuyển họ tên tiếng Việt sang PascalCase ASCII không khoảng trắng (edge case 2).

    Quy trình:
      1. Unicode NFC normalization
      2. Loại bỏ dấu qua unidecode
      3. Tách từng từ theo khoảng trắng / dấu gạch ngang
      4. Title-case từng từ
      5. Ghép liền không khoảng trắng

    >>> _to_pascal_no_space("Nguyễn Văn A")  -> "NguyenVanA"
    >>> _to_pascal_no_space("TRẦN THỊ B")    -> "TranThiB"
    """
    if not name or not name.strip():
        return "KhongTen"
    normalized = unicodedata.normalize("NFC", name.strip())
    ascii_text = unidecode(normalized)
    # Tách theo khoảng trắng, gạch ngang, gạch dưới
    parts = re.split(r"[\s\-_]+", ascii_text)
    pascal = "".join(word.capitalize() for word in parts if word)
    # Chỉ giữ ký tự chữ và số
    pascal = re.sub(r"[^A-Za-z0-9]", "", pascal)
    return pascal or "KhongTen"


def _sanitize_cccd(cccd: str) -> str:
    """
    Kiểm tra và làm sạch số CCCD / CMND.
    Chỉ giữ ký tự số. Không validate độ dài (để linh hoạt với CMND 9 số).
    """
    digits_only = re.sub(r"\D", "", cccd.strip())
    if not digits_only:
        raise ValueError(f"Số CCCD/CMND không hợp lệ (rỗng sau khi lọc): {cccd!r}")
    return digits_only


# ── Lớp chính ─────────────────────────────────────────────────────────────────

class PartyPathBuilder:
    """
    Xây dựng đường dẫn thư mục đầu ra cho một đảng viên cụ thể.

    Cấu trúc:
        <base_output_dir>/[M1].[M2].[M3].[M4].[M5]/[SoCCCD]_[HoTenPascalCase]/

    Ví dụ:
        base_output_dir = Path("output")
        m_codes = ("93", "15", "0", "1", "2")
        cccd = "012345678901"
        ho_ten = "Nguyễn Văn A"
        -> output/93.015.000.001.002/012345678901_NguyenVanA/
    """

    def __init__(
        self,
        base_output_dir: str | Path,
        m1: str | int,
        m2: str | int,
        m3: str | int,
        m4: str | int,
        m5: str | int,
        so_cccd: str,
        ho_ten_dang_vien: str,
    ) -> None:
        """
        Args:
            base_output_dir    : Thư mục gốc chứa toàn bộ output.
            m1                 : Mã cấp ủy cấp TW (2 chữ số).
            m2, m3, m4, m5     : Mã cấp ủy cấp dưới (3 chữ số mỗi cấp).
            so_cccd            : Số CCCD/CMND của đảng viên.
            ho_ten_dang_vien   : Họ tên đầy đủ (có thể có dấu tiếng Việt).
        """
        self.base_output_dir = Path(base_output_dir)

        # Edge case 1: zero-padding M1 (2 chữ số) và M2–M5 (3 chữ số mỗi cấp)
        try:
            self._m1 = _fmt_m1(m1)
            self._m2 = _fmt_mx(m2)
            self._m3 = _fmt_mx(m3)
            self._m4 = _fmt_mx(m4)
            self._m5 = _fmt_mx(m5)
        except ValueError as exc:
            logger.error("[path_builder] Mã cấp ủy không hợp lệ: {}", exc)
            raise

        try:
            self._cccd = _sanitize_cccd(so_cccd)
        except ValueError as exc:
            logger.error("[path_builder] CCCD không hợp lệ: {}", exc)
            raise

        # Edge case 2 (thư mục): PascalCase, không khoảng trắng
        self._ho_ten_pascal = _to_pascal_no_space(ho_ten_dang_vien)

        logger.debug(
            "[path_builder] Khởi tạo: M1={} M2={} M3={} M4={} M5={} "
            "CCCD={} HoTen={}",
            self._m1, self._m2, self._m3, self._m4, self._m5,
            self._cccd, self._ho_ten_pascal,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def cap_uy_segment(self) -> str:
        """
        Chuỗi mã cấp ủy dạng "[M1].[M2].[M3].[M4].[M5]".
        VD: "93.015.000.001.002"
        """
        return f"{self._m1}.{self._m2}.{self._m3}.{self._m4}.{self._m5}"

    @property
    def member_folder_name(self) -> str:
        """
        Tên thư mục đảng viên "[SoCCCD]_[HoTenPascalCase]".
        VD: "012345678901_NguyenVanA"
        """
        return f"{self._cccd}_{self._ho_ten_pascal}"

    @property
    def cap_uy_dir(self) -> Path:
        """
        Đường dẫn thư mục cấp ủy (chưa tạo trên disk).
        VD: output/93.015.000.001.002/
        """
        return self.base_output_dir / self.cap_uy_segment

    @property
    def member_dir(self) -> Path:
        """
        Đường dẫn thư mục đảng viên (chưa tạo trên disk).
        VD: output/93.015.000.001.002/012345678901_NguyenVanA/
        """
        return self.cap_uy_dir / self.member_folder_name

    # ── Actions ───────────────────────────────────────────────────────────────

    def ensure_dirs(self) -> Path:
        """
        Tạo thư mục đảng viên (và toàn bộ cha) nếu chưa tồn tại.
        Trả về Path của member_dir.
        """
        try:
            self.member_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                "[path_builder] Thư mục sẵn sàng: {}", self.member_dir
            )
        except OSError as exc:
            logger.error(
                "[path_builder] Không thể tạo thư mục {}: {}", self.member_dir, exc
            )
            raise
        return self.member_dir

    def resolve_file_path(self, filename: str) -> Path:
        """
        Trả về đường dẫn đầy đủ cho một file nằm trong member_dir.
        Không tạo file hay thư mục.

        Args:
            filename: Tên tệp (kể cả phần mở rộng), VD "01.Ly lich nguoi xin vao dang.pdf"
        """
        return self.member_dir / filename

    def __repr__(self) -> str:
        return (
            f"PartyPathBuilder("
            f"cap_uy={self.cap_uy_segment!r}, "
            f"member={self.member_folder_name!r})"
        )


# ── Hàm tiện ích nhanh (functional API) ─────────────────────────────────────

def build_member_dir(
    base_output_dir: str | Path,
    m1: str | int,
    m2: str | int,
    m3: str | int,
    m4: str | int,
    m5: str | int,
    so_cccd: str,
    ho_ten_dang_vien: str,
    *,
    create: bool = False,
) -> Path:
    """
    Hàm rút gọn: xây dựng (và tuỳ chọn tạo) thư mục đảng viên.

    Args:
        base_output_dir : Thư mục gốc.
        m1…m5           : Mã cấp ủy.
        so_cccd         : Số CCCD/CMND.
        ho_ten_dang_vien: Họ tên (có dấu OK).
        create          : Nếu True, gọi ensure_dirs() để tạo trên disk.

    Returns:
        Path thư mục đảng viên.
    """
    builder = PartyPathBuilder(
        base_output_dir, m1, m2, m3, m4, m5, so_cccd, ho_ten_dang_vien
    )
    if create:
        return builder.ensure_dirs()
    return builder.member_dir


# ── Smoke-test CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True)

    test_cases = [
        # (m1, m2, m3, m4, m5, cccd, ho_ten)
        ("93", "15",  "0",  "1",  "2",  "012345678901", "Nguyễn Văn A"),
        ("3",  "0",   "0",  "0",  "0",  "123456789",   "TRẦN THỊ B"),
        ("99", "999", "1",  "20", "300","098765432100", "Lê Thị Cẩm Nhung"),
        ("1",  "5",   "10", "99", "100","001122334455", "Phạm Văn Đức"),
    ]

    print(f"\n{'=' * 72}")
    print("  party_path_builder — smoke test")
    print(f"{'=' * 72}")
    for m1, m2, m3, m4, m5, cccd, ho_ten in test_cases:
        builder = PartyPathBuilder(
            Path("output"), m1, m2, m3, m4, m5, cccd, ho_ten
        )
        print(f"\n  Input  : M1={m1!r} M2={m2!r} M3={m3!r} M4={m4!r} M5={m5!r}")
        print(f"           CCCD={cccd!r}  HoTen={ho_ten!r}")
        print(f"  cap_uy : {builder.cap_uy_segment}")
        print(f"  member : {builder.member_folder_name}")
        print(f"  path   : {builder.member_dir}")
        sample_file = builder.resolve_file_path("01.Ly lich nguoi xin vao dang.pdf")
        print(f"  file   : {sample_file}")
