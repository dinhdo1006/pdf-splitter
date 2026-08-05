"""Heuristic Vietnamese diacritic restoration for TrOCR output."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


# Longest phrases first — built-in document keyword mapping (no-diacritic → restored)
_BUILTIN_MAPPING: dict[str, str] = {
    # Hồ sơ đảng viên
    "so yeu ly lich dang vien": "Sơ Yếu Lý Lịch Đảng Viên",
    "so yeu ly lich": "Sơ Yếu Lý Lịch",
    "ly lich dang vien": "Lý Lịch Đảng Viên",
    "ly lich": "Lý Lịch",
    "dang vien": "Đảng Viên",
    "muc luc": "Mục Lục",
    "phieu dang vien": "Phiếu Đảng Viên",
    "quyet dinh ket nap": "Quyết Định Kết Nạp",
    "ban tu kiem diem": "Bản Tự Kiểm Điểm",
    "dang cong san viet nam": "Đảng Cộng Sản Việt Nam",
    # Multi-word phrases (longest first priority via sorted lookup)
    "hop dong lao dong": "Hợp Đồng Lao Động",
    "hop dong mua ban": "Hợp Đồng Mua Bán",
    "bien ban ban giao": "Biên Bản Bàn Giao",
    "bien ban nghiem thu": "Biên Bản Nghiệm Thu",
    "giay chung nhan": "Giấy Chứng Nhận",
    "giay xac nhan": "Giấy Xác Nhận",
    "giay uy quyen": "Giấy Ủy Quyền",
    "giay de nghi": "Giấy Đề Nghị",
    "bang thanh toan": "Bảng Thanh Toán",
    "don de nghi": "Đơn Đề Nghị",
    "phieu thu": "Phiếu Thu",
    "phieu chi": "Phiếu Chi",
    "hop dong": "Hợp Đồng",
    "bien ban": "Biên Bản",
    "hoa don": "Hóa Đơn",
    "quyet dinh": "Quyết Định",
    "thong bao": "Thông Báo",
    "to trinh": "Tờ Trình",
    "bao cao": "Báo Cáo",
    "phu luc": "Phụ Lục",
    "bang ke": "Bảng Kê",
    "cong van": "Công Văn",
    "chi thi": "Chỉ Thị",
    "nghi quyet": "Nghị Quyết",
    "nghi dinh": "Nghị Định",
    "thong tu": "Thông Tư",
    "don xin": "Đơn Xin",
    "phieu": "Phiếu",
}


class VietnameseTextRestorer:
    """
    Heuristic restorer — KHÔNG dùng ML, chỉ dùng từ điển và rules.
    Mục tiêu: phục hồi đủ để nhận dạng loại tài liệu, không cần hoàn hảo.
    """

    def __init__(self, dict_path: str | None = None) -> None:
        """
        Load Vietnamese word frequency dictionary nếu có.
        Nếu không có dict_path, dùng built-in keyword mapping.
        """
        self._mapping: dict[str, str] = dict(_BUILTIN_MAPPING)

        if dict_path:
            path = Path(dict_path)
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        extra = json.load(f)
                    if isinstance(extra, dict):
                        # Normalize keys to lowercase
                        for k, v in extra.items():
                            self._mapping[str(k).lower().strip()] = str(v)
                        logger.info(f"Loaded {len(extra)} entries from {path}")
                except Exception as exc:
                    logger.warning(f"Failed to load dict {path}: {exc}")

        # Sort keys by length descending for longest-match-first
        self._sorted_keys = sorted(self._mapping.keys(), key=len, reverse=True)

    def restore(self, text: str) -> str:
        """
        Cố phục hồi dấu cho text tiếng Việt không dấu.
        Longest match trong built-in mapping; nếu không match giữ nguyên.
        """
        if not text or not text.strip():
            return text

        lowered = text.lower()
        result = lowered

        for key in self._sorted_keys:
            if key in result:
                result = result.replace(key, self._mapping[key])

        # If nothing changed, return original (preserve caller casing)
        if result == lowered:
            return text.strip()

        return result.strip()


# Quick test
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    restorer = VietnameseTextRestorer()
    samples = [
        "hop dong lao dong",
        "bien ban nghiem thu so 12",
        "hoa don gtgt",
        "nguyen van a",
        "BAO CAO",
        "phieu thu 500000",
        "",
    ]
    for s in samples:
        print(f"{s!r:40} → {restorer.restore(s)!r}")
