"""
pipeline/llm_referee.py — Local LLM Referee using Ollama /api/chat for ambiguous
boundary detection.

Tích hợp Circuit Breaker tự động tắt sau N timeout liên tiếp
(N được đọc từ config.LLM_CIRCUIT_OPEN_AFTER / config.LLM_CIRCUIT_RESET_AFTER).
"""

from __future__ import annotations

import json
import re
from typing import Optional

import requests
from loguru import logger

import config


# ── Compact catalog snippet gửi cho LLM (top 30 loại phổ biến nhất) ──────────
_CATALOG_SNIPPET = """\
LY_LICH_NGUOI_XIN_VAO_DANG | LY_LICH_DANG_VIEN | PHIEU_DANG_VIEN
DON_XIN_VAO_DANG | GIAY_GIOI_THIEU_NGUOI_VAO_DANG | PHIEU_BO_SUNG_HO_SO_DANG_VIEN
NGHI_QUYET_DE_NGHI_KET_NAP_CUA_CHI_BO | QUYET_DINH_KET_NAP_DANG_VIEN
GIAY_CHUNG_NHAN_LOP_NHAN_THUC_DANG | GIAY_CHUNG_NHAN_LOP_DANG_VIEN_MOI
BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI | NGHI_QUYET_CONG_NHAN_CHINH_THUC_CHI_BO
QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC | GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI
BAN_TU_KIEM_DIEM_HANG_NAM | QUYET_DINH_KY_LUAT_DANG | QUYET_DINH_KHEN_THUONG
QUYET_DINH_XOA_TEN_DANG_VIEN | PHIEU_BAO_CONG_NHAN_CHINH_THUC
TO_KHAI_DE_NGHI_TRUY_TANG_HUY_HIEU_DANG | QUYET_DINH_TANG_HUY_HIEU_DANG
GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC | QUYET_DINH_CHO_RA_KHOI_DANG | KHAC\
"""

_SYSTEM_PROMPT = f"""\
Bạn là hệ thống phân loại tài liệu hành chính Đảng Cộng sản Việt Nam. Nhiệm vụ:
1. Xác định trang hiện tại là TRANG_MOI (bắt đầu tài liệu mới) hay TRANG_NOI_TIEP.
2. Nếu là TRANG_MOI: xác định loại tài liệu theo DANH MỤC BÊN DƯỚI.
3. Trích xuất năm ban hành/ký kết nếu đọc được (dạng số nguyên 4 chữ số).

DANH MỤC LOẠI TÀI LIỆU — chỉ dùng đúng KEY này, không sáng tạo:
{_CATALOG_SNIPPET}

DẤU HIỆU TRANG_MOI: tiêu đề in hoa, số hiệu biểu mẫu, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA",
logo/con dấu đơn vị, dòng "Số:___/___", ký hiệu mẫu biểu (VD: "Mẫu 1-HSĐV").
DẤU HIỆU TRANG_NOI_TIEP: "Trang X/Y", "Điều X", danh sách đánh số tiếp theo,
câu văn bắt đầu bằng động từ hoặc liên từ không có chủ ngữ.

Trả về JSON hợp lệ. Không markdown. Không giải thích thêm.\
"""

_USER_TEMPLATE = """\
[LOẠI TÀI LIỆU LIỀN TRƯỚC]: {prev_doc_type}
[NĂM TÀI LIỆU TRƯỚC]: {prev_doc_year}
[ĐẦU TRANG {page_num} — văn bản OCR]:
{snippet}

JSON output (điền vào):
{{"is_new_document": true|false,
  "doc_type": "KEY_THEO_DANH_MUC_HOAC_KHAC",
  "doc_year": <số nguyên 4 chữ số hoặc null>,
  "confidence": "high"|"medium"|"low",
  "reasoning": "<1 câu tiếng Việt giải thích>"}}\
"""


class LLMReferee:
    """
    Trọng tài LLM cục bộ (Ollama /api/chat).

    Tích hợp Circuit Breaker tự động tắt sau N timeout liên tiếp
    và tự thử lại sau M trang bỏ qua (N, M đọc từ config).
    """

    def __init__(
        self,
        endpoint: str   = getattr(config, "OLLAMA_ENDPOINT", "http://localhost:11434/api/chat"),
        model:    str   = getattr(config, "OLLAMA_MODEL",    "qwen2.5:3b"),
        timeout:  float = getattr(config, "OLLAMA_TIMEOUT",  10.0),
    ) -> None:
        self.endpoint = endpoint
        self.model    = model
        self.timeout  = timeout

        # Circuit Breaker — thresholds từ config (có fallback cứng)
        self._circuit_open_after:  int = getattr(config, "LLM_CIRCUIT_OPEN_AFTER",  3)
        self._circuit_reset_after: int = getattr(config, "LLM_CIRCUIT_RESET_AFTER", 50)

        self._consecutive_timeouts:     int  = 0
        self._pages_since_circuit_open: int  = 0
        self._circuit_open:             bool = False

        self.is_available: bool = self._check_connection()

    # ── Kết nối ──────────────────────────────────────────────────────

    def _check_connection(self) -> bool:
        """
        Kiểm tra kết nối nhanh tới Ollama Server lúc khởi tạo pipeline.

        Returns:
            True nếu Ollama phản hồi OK, False nếu timeout / lỗi kết nối.
        """
        try:
            base = self.endpoint.split("/api/")[0]
            res  = requests.get(f"{base}/api/version", timeout=3.0)
            if res.status_code == 200:
                logger.info(
                    f"[LLM] Kết nối Ollama OK — model '{self.model}' tại {base}"
                )
                return True
        except Exception as exc:
            logger.warning(
                f"[LLM] Không kết nối được Ollama ({exc}). "
                "Fallback hoàn toàn về Rule-based."
            )
        return False

    # ── Circuit Breaker ───────────────────────────────────────────────

    def _circuit_check(self) -> bool:
        """
        Kiểm tra xem Circuit Breaker có đang mở không.

        Returns:
            True nếu được phép gửi request; False nếu mạch đang ngắt.
        """
        if not self._circuit_open:
            return True
        self._pages_since_circuit_open += 1
        if self._pages_since_circuit_open >= self._circuit_reset_after:
            logger.info(
                f"[LLM] Circuit Breaker: thử kết nối lại sau "
                f"{self._circuit_reset_after} trang bỏ qua..."
            )
            self._circuit_open             = False
            self._pages_since_circuit_open = 0
            return True
        return False

    def _record_timeout(self, page_num: int) -> None:
        """Ghi nhận timeout; mở circuit nếu đạt ngưỡng."""
        self._consecutive_timeouts += 1
        logger.warning(
            f"[LLM] Timeout #{self._consecutive_timeouts} tại trang {page_num}"
        )
        if self._consecutive_timeouts >= self._circuit_open_after:
            self._circuit_open = True
            logger.error(
                f"[LLM] CIRCUIT OPEN — ngắt LLM sau {self._circuit_open_after} "
                "timeout liên tiếp. Toàn bộ trang tiếp theo dùng Rule-based."
            )

    def _record_success(self) -> None:
        """Reset bộ đếm timeout sau một lần thành công."""
        self._consecutive_timeouts = 0

    # ── Core judge ────────────────────────────────────────────────────

    def judge_boundary(
        self,
        page_num:      int,
        current_text:  str,
        prev_doc_type: str          = "CHUA_XAC_DINH",
        prev_doc_year: Optional[int] = None,
    ) -> dict | None:
        """
        Gửi văn bản trang nghi ngờ cho LLM thẩm định qua Ollama /api/chat.

        Args:
            page_num:      Số trang đang xét (1-based).
            current_text:  Văn bản OCR đầu trang (tối đa 400 ký tự được dùng).
            prev_doc_type: Mã loại tài liệu liền trước (từ DocumentGroup.doc_type).
            prev_doc_year: Năm ban hành tài liệu liền trước (hoặc None).

        Returns:
            dict gồm:
                "is_new_document": bool
                "doc_type":        str  (KEY theo catalog hoặc "KHAC")
                "doc_year":        int | None
                "confidence":      str  ("high"|"medium"|"low")
                "reasoning":       str
            hoặc None nếu lỗi / timeout / circuit open.
        """
        if not self.is_available:
            return None
        if not self._circuit_check():
            return None

        snippet = (current_text or "").strip()[:400]
        if not snippet:
            return None

        user_msg = _USER_TEMPLATE.format(
            prev_doc_type = prev_doc_type,
            prev_doc_year = prev_doc_year if prev_doc_year else "không rõ",
            page_num      = page_num,
            snippet       = snippet,
        )

        payload = {
            "model":  self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "options": {
                "temperature": 0.0,
                "num_ctx":     640,
                "num_predict": 80,
            },
        }

        try:
            res = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            res.raise_for_status()
            data = res.json()

            # /api/chat trả về data["message"]["content"]
            raw = data.get("message", {}).get("content", "{}")
            parsed = self._safe_parse(raw)

            if parsed and "is_new_document" in parsed:
                self._record_success()

                # Validate và normalise doc_year
                year_raw = parsed.get("doc_year")
                doc_year: int | None = None
                try:
                    if year_raw is not None:
                        y = int(year_raw)
                        doc_year = y if 1930 <= y <= 2035 else None
                except (ValueError, TypeError):
                    pass

                return {
                    "is_new_document": bool(parsed.get("is_new_document", False)),
                    "doc_type":        str(parsed.get("doc_type", "KHAC")).upper().replace(" ", "_"),
                    "doc_year":        doc_year,
                    "confidence":      str(parsed.get("confidence", "low")),
                    "reasoning":       str(parsed.get("reasoning", "LLM decision")),
                }
            else:
                logger.warning(
                    f"[LLM] Page {page_num}: phản hồi thiếu 'is_new_document' — bỏ qua"
                )

        except requests.exceptions.Timeout:
            self._record_timeout(page_num)
        except requests.exceptions.HTTPError as exc:
            logger.error(f"[LLM] Page {page_num} HTTP error: {exc}")
        except Exception as exc:
            logger.error(
                f"[LLM] Page {page_num} {type(exc).__name__}: {exc}"
            )

        return None

    # ── JSON parser ───────────────────────────────────────────────────

    @staticmethod
    def _safe_parse(text: str) -> dict | None:
        """
        Trích xuất an toàn JSON từ response của LLM.

        Thử json.loads trực tiếp trước; nếu lỗi, tìm block {...} đầu tiên.

        Args:
            text: Chuỗi phản hồi từ LLM (có thể lẫn text rác).

        Returns:
            dict đã parse hoặc None nếu không parse được.
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*?\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return None