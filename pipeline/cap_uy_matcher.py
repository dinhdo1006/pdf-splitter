"""
pipeline/cap_uy_matcher.py
==========================
Tra cứu mã cấp ủy M1–M5 từ văn bản OCR trang PDF.

Luồng:
    OCR text → unidecode → fuzzy-match với cap_uy_catalog.json
    → trả về (m1, m2, m3, m4, m5, ten_khop, score) hoặc None

File catalog: <project_root>/cap_uy_catalog.json
    Điền đơn vị thực tế vào 'don_vi'. Xem hướng dẫn trong file JSON.

Dùng trong identity_extractor.py:
    from pipeline.cap_uy_matcher import match_cap_uy_from_text
    result = match_cap_uy_from_text(full_ocr_text)
    if result:
        ident.m1, ident.m2, ident.m3, ident.m4, ident.m5 = result.m1, ...
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from loguru import logger
from rapidfuzz import fuzz
from unidecode import unidecode

import config

# ── Đường dẫn file catalog ────────────────────────────────────────────────────
_CATALOG_PATH = config.PROJECT_ROOT / "cap_uy_catalog.json"

# ── Ngưỡng mặc định nếu catalog không có ─────────────────────────────────────
_DEFAULT_THRESHOLD = 0.80
_DEFAULT_MIN_LEN = 6


# ── Kết quả tra cứu ──────────────────────────────────────────────────────────

@dataclass
class CapUyMatch:
    m1: str
    m2: str
    m3: str
    m4: str
    m5: str
    ten_khop: str        # Tên alias/chính đã khớp
    ten_chinh: str       # Tên chính thức trong catalog
    score: float         # Độ tương đồng 0.0–1.0
    source: str = "cap_uy_catalog"

    @property
    def cap_uy_segment(self) -> str:
        """VD: '93.015.000.001.015'"""
        from pipeline.party_path_builder import _fmt_m1, _fmt_mx
        return (
            f"{_fmt_m1(self.m1)}.{_fmt_mx(self.m2)}."
            f"{_fmt_mx(self.m3)}.{_fmt_mx(self.m4)}.{_fmt_mx(self.m5)}"
        )


# ── Nội bộ: đọc và cache catalog ─────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_catalog() -> dict:
    """Đọc cap_uy_catalog.json một lần, cache lại."""
    if not _CATALOG_PATH.is_file():
        logger.warning(
            f"[cap_uy] Không tìm thấy catalog: {_CATALOG_PATH}. "
            "Tạo file cap_uy_catalog.json để tra mã cấp ủy tự động."
        )
        return {"don_vi": [], "cau_hinh": {}}
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        n = len(data.get("don_vi") or [])
        logger.info(f"[cap_uy] Đã nạp catalog: {n} đơn vị từ {_CATALOG_PATH.name}")
        return data
    except Exception as exc:
        logger.error(f"[cap_uy] Lỗi đọc catalog: {exc}")
        return {"don_vi": [], "cau_hinh": {}}


def reload_catalog() -> None:
    """Xóa cache để đọc lại catalog (dùng khi file thay đổi lúc runtime)."""
    _load_catalog.cache_clear()
    _load_catalog()


# ── Chuẩn hóa text để so khớp ────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Unidecode → lowercase → bỏ ký tự thừa → chuẩn khoảng trắng."""
    t = unidecode(text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _score(query_norm: str, candidate_norm: str) -> float:
    """Điểm fuzzy cao nhất giữa partial_ratio và token_set_ratio."""
    if not query_norm or not candidate_norm:
        return 0.0
    s1 = fuzz.partial_ratio(candidate_norm, query_norm) / 100.0
    s2 = fuzz.token_set_ratio(candidate_norm, query_norm) / 100.0
    return max(s1, s2)


# ── Hàm chính ─────────────────────────────────────────────────────────────────

def match_cap_uy_from_text(
    ocr_text: str,
    *,
    threshold: Optional[float] = None,
) -> Optional[CapUyMatch]:
    """
    Tìm mã cấp ủy M1–M5 từ đoạn văn bản OCR.

    Args:
        ocr_text  : Văn bản full-text hoặc header từ OCR một trang PDF.
        threshold : Ngưỡng tương đồng tối thiểu (0.0–1.0).
                    None → dùng giá trị trong catalog ('cau_hinh.nguong_khop_mac_dinh').

    Returns:
        CapUyMatch nếu khớp, None nếu không tìm được.
    """
    if not ocr_text or not ocr_text.strip():
        return None

    catalog = _load_catalog()
    cfg = catalog.get("cau_hinh") or {}
    default_thr = float(cfg.get("nguong_khop_mac_dinh", _DEFAULT_THRESHOLD))
    min_len = int(cfg.get("so_ky_tu_ocr_toi_thieu", _DEFAULT_MIN_LEN))
    thr = threshold if threshold is not None else default_thr

    don_vi_list = catalog.get("don_vi") or []
    if not don_vi_list:
        return None

    # Giới hạn text đầu vào (lấy 2000 ký tự đầu — vùng header/tiêu đề)
    query_norm = _normalize(ocr_text[:2000])
    if len(query_norm) < min_len:
        return None

    best_match: Optional[CapUyMatch] = None
    best_score = 0.0

    for entry in don_vi_list:
        # Bỏ qua mục ghi chú / mẫu chưa điền
        if not entry.get("m1") or not entry.get("ten_chinh"):
            continue

        entry_thr = float(entry.get("nguong_khop", thr))

        # Tập hợp tất cả tên cần thử (ten_chinh + aliases)
        candidates: list[tuple[str, str]] = []
        ten_chinh = entry.get("ten_chinh", "")
        if ten_chinh:
            candidates.append((ten_chinh, _normalize(ten_chinh)))
        for alias in (entry.get("aliases") or []):
            if alias:
                candidates.append((alias, _normalize(alias)))

        for ten_label, cand_norm in candidates:
            if len(cand_norm) < min_len:
                continue
            sc = _score(query_norm, cand_norm)
            if sc >= entry_thr and sc > best_score:
                best_score = sc
                best_match = CapUyMatch(
                    m1=str(entry.get("m1", "0")),
                    m2=str(entry.get("m2", "0")),
                    m3=str(entry.get("m3", "0")),
                    m4=str(entry.get("m4", "0")),
                    m5=str(entry.get("m5", "0")),
                    ten_khop=ten_label,
                    ten_chinh=ten_chinh,
                    score=sc,
                )

    if best_match:
        logger.info(
            f"[cap_uy] Khớp: '{best_match.ten_khop}' "
            f"→ {best_match.cap_uy_segment} (score={best_match.score:.2f})"
        )
    else:
        logger.debug("[cap_uy] Không khớp được đơn vị nào từ OCR text.")

    return best_match


def match_cap_uy_from_signals(
    signals: dict,
    *,
    threshold: Optional[float] = None,
    max_pages: int = 10,
) -> Optional[CapUyMatch]:
    """
    Quét PageSignal nhiều trang (ưu tiên trang đầu) để tìm mã cấp ủy.

    Args:
        signals   : dict[page_num → PageSignal] từ pipeline chính.
        threshold : Ngưỡng tương đồng.
        max_pages : Chỉ quét tối đa N trang đầu (header tài liệu).

    Returns:
        CapUyMatch đầu tiên tìm được, hoặc None.
    """
    for page_num in sorted(signals.keys())[:max_pages]:
        sig = signals[page_num]
        header = getattr(sig, "header_text", "") or ""
        full = getattr(sig, "full_text", "") or ""
        blob = header + "\n" + full[:800]
        result = match_cap_uy_from_text(blob, threshold=threshold)
        if result:
            logger.info(
                f"[cap_uy] Tìm thấy tại trang {page_num}: "
                f"{result.cap_uy_segment} ('{result.ten_chinh}')"
            )
            return result
    return None


# ── Smoke-test CLI ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True)

    samples = [
        "ĐẢNG CỘNG SẢN VIỆT NAM\nBan Tổ chức Trung ương\nPhiếu đảng viên",
        "Tỉnh ủy Hà Nội\nHọ tên: Nguyễn Văn A",
        "ĐẢNG ỦY KHỐI CƠ QUAN TỈNH\nBản tự kiểm điểm hằng năm",
        "Văn bản không liên quan đến cấp ủy nào hết",
    ]

    print(f"\n{'=' * 60}")
    print("  cap_uy_matcher — smoke test")
    print(f"{'=' * 60}")
    for sample in samples:
        print(f"\nInput : {sample[:60]!r}...")
        result = match_cap_uy_from_text(sample)
        if result:
            print(f"  → {result.cap_uy_segment}  ('{result.ten_chinh}', score={result.score:.2f})")
        else:
            print("  → Không khớp")
