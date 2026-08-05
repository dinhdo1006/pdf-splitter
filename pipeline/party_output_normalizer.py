"""
pipeline/party_output_normalizer.py
=====================================
Orchestrator tổng hợp — chuẩn hóa TOÀN BỘ đầu ra cho một hồ sơ đảng viên
theo đúng 5 edge case nghiệp vụ (Hướng dẫn 1361-CV/BTCTW).

Module này kết nối:
  - party_catalog.py       -> tra cứu 104 loại tài liệu
  - party_path_builder.py  -> xây dựng cấu trúc thư mục M1.M2.M3.M4.M5/CCCD_HoTen/
  - party_filename_resolver.py -> giải quyết tên tệp PDF (STT padding + n=1/n>=2)

Kết quả cuối cùng là một danh sách NormalizedOutputItem, mỗi item chứa:
  - Đường dẫn thư mục đích (member_dir)
  - Tên tệp đích chuẩn hóa (filename)
  - Đường dẫn đích đầy đủ (dest_path)
  - Metadata tài liệu (STT, tên, độ ưu tiên)

Ví dụ sử dụng:
    normalizer = PartyOutputNormalizer(
        base_output_dir = Path("output"),
        m1="93", m2="15", m3="0", m4="1", m5="2",
        so_cccd="012345678901",
        ho_ten_dang_vien="Nguyễn Văn A",
    )
    items = normalizer.normalize(detected_doc_keys)
    normalizer.ensure_output_dirs()
    for item in items:
        # copy/rename source PDF to item.dest_path
        print(item.dest_path)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from loguru import logger

from pipeline.party_catalog import (
    PARTY_DOC_CATALOG,
    fmt_stt,
    lookup_by_key,
    priority_from_stt,
)
from pipeline.party_filename_resolver import (
    PartyFilenameResolver,
    ResolvedFilename,
    STRIP_FILE_SPACES,
)
from pipeline.party_path_builder import PartyPathBuilder


# ── Dataclass kết quả chuẩn hóa cho một tài liệu ────────────────────────────

@dataclass
class NormalizedOutputItem:
    """Toàn bộ thông tin chuẩn hóa của một tài liệu trong hồ sơ."""

    # ── Định danh ─────────────────────────────────────────────
    doc_type_key: str           # Mã nội bộ  (VD "LY_LICH_DANG_VIEN")
    stt_formatted: str          # STT đã pad (VD "02", "100")
    ten_tai_lieu: str           # Tên không dấu (VD "Ly lich dang vien")
    do_uu_tien: int             # 1, 2, hoặc 3

    # ── Số thứ tự xuất hiện ────────────────────────────────────
    occurrence_index: int       # Lần thứ N trong hồ sơ (1-based)
    total_occurrences: int      # Tổng số lần loại này có trong hồ sơ

    # ── Đường dẫn đích ────────────────────────────────────────
    member_dir: Path            # Thư mục đảng viên
    filename: str               # Tên tệp đầy đủ kể cả ".pdf"
    dest_path: Path             # member_dir / filename

    @property
    def is_duplicate(self) -> bool:
        return self.total_occurrences >= 2

    def to_dict(self) -> dict:
        """Chuyển sang dict tuần tự hóa được (Path -> str)."""
        d = asdict(self)
        d["member_dir"] = str(self.member_dir)
        d["dest_path"] = str(self.dest_path)
        return d


# ── Lớp Orchestrator chính ────────────────────────────────────────────────────

class PartyOutputNormalizer:
    """
    Orchestrator chuẩn hóa đầu ra cho toàn bộ hồ sơ của MỘT đảng viên.

    Kết hợp:
      • PartyPathBuilder      -> cấu trúc thư mục
      • PartyFilenameResolver -> tên tệp (STT padding + n=1/n>=2)
      • PARTY_DOC_CATALOG     -> metadata tài liệu & priority defaults

    Edge cases được xử lý hoàn toàn tự động:
      1. Zero-padding M1 (2 số) & M2-M5 (3 số)          [trong PathBuilder]
      2. Tên thư mục PascalCase không dấu không space   [trong PathBuilder]
         Tên tệp giữ space (STRIP_FILE_SPACES=False mặc định)
      3. Đuôi ".N" khi n>=2, KHÔNG ".1" khi n=1         [trong FilenameResolver]
      4. Zero-padding STT (01-99: 2 số; 100-104: 3 số)  [trong fmt_stt()]
      5. Priority defaults theo dải STT                 [trong priority_from_stt()]
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
        strip_file_spaces: bool = STRIP_FILE_SPACES,
    ) -> None:
        """
        Args:
            base_output_dir   : Thư mục gốc chứa toàn bộ output.
            m1                : Mã cấp ủy TW (2 chữ số sau padding).
            m2, m3, m4, m5    : Mã cấp ủy cấp dưới (3 chữ số sau padding).
            so_cccd           : Số CCCD/CMND (chỉ lấy phần số).
            ho_ten_dang_vien  : Họ tên đầy đủ (có dấu OK).
            strip_file_spaces : True = viết liền tên tệp; False = giữ khoảng trắng.
        """
        self._path_builder = PartyPathBuilder(
            base_output_dir=base_output_dir,
            m1=m1, m2=m2, m3=m3, m4=m4, m5=m5,
            so_cccd=so_cccd,
            ho_ten_dang_vien=ho_ten_dang_vien,
        )
        self._filename_resolver = PartyFilenameResolver(
            strip_spaces=strip_file_spaces
        )
        self._items: list[NormalizedOutputItem] = []

        logger.info(
            "[output_normalizer] Khởi tạo cho đảng viên: {} | thư mục: {}",
            self._path_builder.member_folder_name,
            self._path_builder.member_dir,
        )

    # ── API chính ─────────────────────────────────────────────────────────────

    def normalize(
        self,
        doc_type_keys: Sequence[str],
    ) -> list[NormalizedOutputItem]:
        """
        Chuẩn hóa toàn bộ danh sách tài liệu của một hồ sơ.

        Args:
            doc_type_keys: Danh sách mã tài liệu theo thứ tự xuất hiện.
                           Có thể có trùng lặp (VD "BAN_TU_KIEM_DIEM_HANG_NAM" x3).

        Returns:
            Danh sách NormalizedOutputItem theo cùng thứ tự.

        Raises:
            KeyError   : Nếu có key không tồn tại trong catalog.
            ValueError : Nếu dữ liệu đầu vào không hợp lệ.
        """
        keys = [k.upper() for k in doc_type_keys]

        # Validate toàn bộ trước khi xử lý
        unknown = [k for k in keys if k not in PARTY_DOC_CATALOG]
        if unknown:
            logger.error(
                "[output_normalizer] Key không hợp lệ: {}", unknown
            )
            raise KeyError(
                f"Các doc_type_key không có trong catalog: {unknown}"
            )

        logger.info(
            "[output_normalizer] Bắt đầu normalize {} tài liệu ({} loại phân biệt).",
            len(keys),
            len(set(keys)),
        )

        # Giải quyết toàn bộ tên tệp
        resolved_list: list[ResolvedFilename] = self._filename_resolver.resolve_all(keys)

        member_dir = self._path_builder.member_dir
        self._items = []

        for resolved in resolved_list:
            key = resolved.doc_type_key
            stt_raw, ten_raw, uu_tien_catalog = PARTY_DOC_CATALOG[key]

            # Edge case 5: priority fallback nếu ô trống
            uu_tien = uu_tien_catalog if uu_tien_catalog else priority_from_stt(stt_raw)

            dest_path = member_dir / resolved.filename

            item = NormalizedOutputItem(
                doc_type_key=key,
                stt_formatted=resolved.stt_formatted,
                ten_tai_lieu=resolved.ten_tai_lieu,
                do_uu_tien=uu_tien,
                occurrence_index=resolved.occurrence_index,
                total_occurrences=resolved.total_occurrences,
                member_dir=member_dir,
                filename=resolved.filename,
                dest_path=dest_path,
            )
            self._items.append(item)

            logger.debug(
                "[output_normalizer] {} -> {!r} (P{}, occ={}/{})",
                key,
                resolved.filename,
                uu_tien,
                resolved.occurrence_index,
                resolved.total_occurrences,
            )

        logger.info(
            "[output_normalizer] normalize() hoàn tất: {} tệp đã chuẩn hóa.",
            len(self._items),
        )
        return self._items

    # ── Helpers ───────────────────────────────────────────────────────────────

    def ensure_output_dirs(self) -> Path:
        """
        Tạo thư mục đảng viên trên disk (và toàn bộ cha).
        Trả về Path của member_dir.
        """
        return self._path_builder.ensure_dirs()

    @property
    def member_dir(self) -> Path:
        """Đường dẫn thư mục đảng viên."""
        return self._path_builder.member_dir

    @property
    def cap_uy_segment(self) -> str:
        """Chuỗi mã cấp ủy VD '93.015.000.001.002'."""
        return self._path_builder.cap_uy_segment

    @property
    def member_folder_name(self) -> str:
        """Tên thư mục đảng viên VD '012345678901_NguyenVanA'."""
        return self._path_builder.member_folder_name

    @property
    def items(self) -> list[NormalizedOutputItem]:
        """Danh sách kết quả từ lần normalize() cuối cùng (read-only copy)."""
        return list(self._items)

    def items_by_priority(self) -> list[NormalizedOutputItem]:
        """Trả về items sắp xếp theo Do_uu_tien (1->3) rồi STT."""
        return sorted(
            self._items,
            key=lambda x: (x.do_uu_tien, int(x.stt_formatted)),
        )

    def duplicates(self) -> list[NormalizedOutputItem]:
        """Trả về chỉ các tài liệu bị lặp (total_occurrences >= 2)."""
        return [i for i in self._items if i.is_duplicate]

    # ── Xuất manifest JSON ────────────────────────────────────────────────────

    def write_manifest(
        self,
        output_path: str | Path | None = None,
    ) -> Path:
        """
        Ghi file manifest JSON cho hồ sơ đảng viên.

        Args:
            output_path: Đường dẫn tệp JSON. Mặc định là
                         member_dir/manifest_ho_so.json

        Returns:
            Path của tệp manifest đã ghi.

        Raises:
            RuntimeError: Nếu chưa gọi normalize().
        """
        if not self._items:
            raise RuntimeError(
                "Chưa có dữ liệu. Hãy gọi normalize() trước write_manifest()."
            )

        if output_path is None:
            output_path = self.member_dir / "manifest_ho_so.json"
        else:
            output_path = Path(output_path)

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cap_uy_segment": self.cap_uy_segment,
            "member_folder": self.member_folder_name,
            "member_dir": str(self.member_dir),
            "total_documents": len(self._items),
            "duplicate_types": len(self.duplicates()),
            "documents": [item.to_dict() for item in self._items],
        }

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            logger.info(
                "[output_normalizer] Manifest đã ghi: {}", output_path
            )
        except OSError as exc:
            logger.error(
                "[output_normalizer] Không thể ghi manifest {}: {}",
                output_path, exc,
            )
            raise

        return output_path

    def print_summary(self) -> None:
        """In bảng tổng kết ra console (qua loguru)."""
        if not self._items:
            logger.info("[output_normalizer] Chưa có item nào. Gọi normalize() trước.")
            return

        col_stt = 5
        col_p = 3
        col_oc = 6
        col_file = max(40, max(len(i.filename) for i in self._items) + 2)

        header = (
            f"| {'STT':<{col_stt}} | {'P':<{col_p}} | {'n/tot':<{col_oc}} "
            f"| {'Tên tệp':<{col_file}} |"
        )
        sep = (
            f"|{'-'*(col_stt+2)}|{'-'*(col_p+2)}|{'-'*(col_oc+2)}"
            f"|{'-'*(col_file+2)}|"
        )

        logger.info("")
        logger.info(
            "=== HỒ SƠ ĐẢNG VIÊN: {} | {} ===",
            self.cap_uy_segment,
            self.member_folder_name,
        )
        logger.info(header)
        logger.info(sep)
        for item in self._items:
            oc = f"{item.occurrence_index}/{item.total_occurrences}"
            logger.info(
                "| {:<{c_stt}} | {:<{c_p}} | {:<{c_oc}} | {:<{c_file}} |",
                item.stt_formatted, item.do_uu_tien, oc, item.filename,
                c_stt=col_stt, c_p=col_p, c_oc=col_oc, c_file=col_file,
            )
        logger.info("")
        dup_count = len(self.duplicates())
        logger.info(
            "Tổng: {} tài liệu | {} loại phân biệt | {} tài liệu lặp",
            len(self._items),
            len(set(i.doc_type_key for i in self._items)),
            dup_count,
        )


# ── Hàm tiện ích nhanh (functional API) ─────────────────────────────────────

def normalize_member_output(
    base_output_dir: str | Path,
    m1: str | int,
    m2: str | int,
    m3: str | int,
    m4: str | int,
    m5: str | int,
    so_cccd: str,
    ho_ten_dang_vien: str,
    doc_type_keys: Sequence[str],
    *,
    strip_file_spaces: bool = STRIP_FILE_SPACES,
    create_dirs: bool = False,
    write_manifest: bool = False,
) -> list[NormalizedOutputItem]:
    """
    Hàm rút gọn: khởi tạo PartyOutputNormalizer, normalize, và tuỳ chọn
    tạo thư mục + ghi manifest trong một lần gọi.

    Returns:
        list[NormalizedOutputItem]
    """
    norm = PartyOutputNormalizer(
        base_output_dir=base_output_dir,
        m1=m1, m2=m2, m3=m3, m4=m4, m5=m5,
        so_cccd=so_cccd,
        ho_ten_dang_vien=ho_ten_dang_vien,
        strip_file_spaces=strip_file_spaces,
    )
    items = norm.normalize(doc_type_keys)
    if create_dirs:
        norm.ensure_output_dirs()
    if write_manifest:
        norm.write_manifest()
    return items


# ── Smoke-test CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True)

    # Giả lập hồ sơ đảng viên điển hình
    detected_docs = [
        "LY_LICH_NGUOI_XIN_VAO_DANG",
        "LY_LICH_DANG_VIEN",
        "PHIEU_DANG_VIEN",
        "DON_XIN_VAO_DANG",
        "GIAY_CHUNG_NHAN_LOP_NHAN_THUC_DANG",
        "NGHI_QUYET_DE_NGHI_KET_NAP_CUA_CHI_BO",
        "QUYET_DINH_KET_NAP_DANG_VIEN",
        "GIAY_CHUNG_NHAN_LOP_DANG_VIEN_MOI",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI",
        "NGHI_QUYET_CONG_NHAN_CHINH_THUC_CHI_BO",
        "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC",
        # Tài liệu lặp (n=2)
        "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI",
        "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI",
        # Kiểm điểm hằng năm (n=3)
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        # STT 100 và 104
        "PHIEU_BAO_CONG_NHAN_CHINH_THUC",
        "TO_KHAI_DE_NGHI_TRUY_TANG_HUY_HIEU_DANG",
    ]

    norm = PartyOutputNormalizer(
        base_output_dir=Path("output"),
        m1="93", m2="15", m3="0", m4="1", m5="2",
        so_cccd="012345678901",
        ho_ten_dang_vien="Nguyễn Văn A",
        strip_file_spaces=False,
    )
    items = norm.normalize(detected_docs)
    norm.print_summary()

    print(f"\n--- Đường dẫn thư mục ---")
    print(f"  cap_uy_dir : {norm.member_dir.parent}")
    print(f"  member_dir : {norm.member_dir}")

    print(f"\n--- Các tài liệu lặp ---")
    for dup in norm.duplicates():
        print(f"  [{dup.stt_formatted}] {dup.filename}")

    print(f"\n--- Kiểm tra edge case 3 (n=1 vs n>=2) ---")
    for item in items:
        n_flag = f"n={item.total_occurrences}"
        suffix_flag = (
            "CÓ hậu tố số" if item.is_duplicate else "KHÔNG hậu tố"
        )
        print(f"  {n_flag:<6}  {suffix_flag:<20}  {item.filename}")

    print(f"\n--- Edge case 4 (zero-padding STT) ---")
    for item in items:
        print(f"  raw_stt={PARTY_DOC_CATALOG[item.doc_type_key][0]:>3}  "
              f"formatted={item.stt_formatted}")

    print(f"\n--- Edge case 5 (priority defaults) ---")
    for item in items:
        print(f"  [{item.stt_formatted}] P{item.do_uu_tien}  {item.doc_type_key}")

    # Test functional API
    print(f"\n--- normalize_member_output() functional API ---")
    items2 = normalize_member_output(
        base_output_dir=Path("output"),
        m1=3, m2=0, m3=0, m4=0, m5=0,
        so_cccd="123456789",
        ho_ten_dang_vien="TRẦN THỊ B",
        doc_type_keys=detected_docs[:5],
        strip_file_spaces=True,
    )
    for i in items2:
        print(f"  {i.dest_path}")
