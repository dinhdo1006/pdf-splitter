"""
pipeline/continuation_validator.py
==================================
Xác nhận kế thừa hợp lệ (CONFIRMED_CONTINUATION) — chống nuốt tài liệu.

Trang chỉ được gộp vào tài liệu liền trước khi đạt ít nhất 1 điều kiện:
  Rule 1 — Syntactic continuity (số trang liên tiếp / câu viết dở)
  Rule 2 — Layout continuity (bảng/ô bị cắt sát đáy)
  Rule 3 — LLM semantic (chỉ khi Rule 1–2 inconclusive)

Fail / timeout LLM → False → ORPHAN_PAGE (không merge).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import requests
from loguru import logger
from unidecode import unidecode

import config
from pipeline.signal_extractor import PageSignal


# Loại form thường nhiều trang — cho phép soft layout continuation
MULTI_PAGE_FORM_TYPES = frozenset(
    {
        "LY_LICH_DANG_VIEN",
        "LY_LICH_NGUOI_XIN_VAO_DANG",
        "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
        "PHIEU_DANG_VIEN",
        "BAN_TU_KIEM_DIEM_HANG_NAM",
        "BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI",
        "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON",
    }
)

_PAGE_NUM_RE = re.compile(
    r"(?:trang|page)\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)
_BARE_PAGE_RE = re.compile(r"^\s*[-–]?\s*(\d+)\s*[-–]?\s*$")
_SENTENCE_END_RE = re.compile(r"[.!?…。]$")
_SIGNATURE_HINTS = (
    "ky ten",
    "chu ky",
    "chữ ký",
    "chu ky",
    "da ky",
    "đã ký",
    "tm.",
    "kt.",
    "thu truong",
    "bi thu",
    "chi uy",
    "dang uy",
    "xac nhan",
)

_CONT_SYSTEM = """\
Bạn xác định trang hiện tại có phải phần TIẾP theo của tài liệu liền trước không.
Trả JSON thuần: {"is_continuation": true|false, "confidence": 0.0-1.0, "reasoning": "1 câu"}.
Chỉ true khi chắc chắn cùng một văn bản đang viết tiếp. Nghi ngờ → false.\
"""

_CONT_USER = """\
[CUỐI TRANG TRƯỚC]:
{prev_tail}

[ĐẦU TRANG HIỆN TẠI]:
{curr_head}

JSON:\
"""


@dataclass
class ContinuationVerdict:
    """Kết quả kiểm tra kế thừa."""

    is_continuation: bool
    rule: str  # "rule1" | "rule2" | "rule3" | "none"
    reason: str
    confidence: float = 0.0


class ContinuationValidator:
    """
    Kiểm tra điều kiện kế thừa hợp lệ giữa trang i-1 và trang i.
    """

    def __init__(
        self,
        llm_min_confidence: float = getattr(
            config, "CONTINUATION_LLM_MIN_CONFIDENCE", 0.75
        ),
        enable_llm: bool = getattr(config, "ENABLE_CONTINUATION_LLM", True),
    ) -> None:
        self.llm_min_confidence = llm_min_confidence
        self.enable_llm = enable_llm

    def validate(
        self,
        prev: Optional[PageSignal],
        curr: PageSignal,
        llm_referee: Any = None,
        has_open_group: bool = False,
        open_doc_type: Optional[str] = None,
    ) -> ContinuationVerdict:
        """
        Returns:
            ContinuationVerdict — is_continuation=False → gọi ORPHAN.
        """
        if prev is None:
            return ContinuationVerdict(
                False, "none", "no_previous_page", 0.0
            )

        # Không gộp nếu trang hiện tại đã khớp catalog (đó là NEW, không phải cont)
        if curr.has_doc_keyword and curr.matched_doc_type:
            return ContinuationVerdict(
                False, "none", "curr_has_catalog_hit", 0.0
            )

        # Soft: mục lục / mục form / hướng dẫn LL khi đang mở lý lịch
        soft = self._soft_ly_lich_absorb(curr, has_open_group, open_doc_type)
        if soft.is_continuation:
            return soft

        r1 = self._rule1_syntactic(prev, curr)
        if r1.is_continuation:
            logger.debug(
                f"Page {curr.page_num}: continuation via {r1.rule} — {r1.reason}"
            )
            return r1

        r2 = self._rule2_layout(prev, curr, has_open_group, open_doc_type)
        if r2.is_continuation:
            logger.debug(
                f"Page {curr.page_num}: continuation via {r2.rule} — {r2.reason}"
            )
            return r2

        inconclusive = (not r1.is_continuation) and (not r2.is_continuation)
        if inconclusive and self.enable_llm and llm_referee is not None:
            r3 = self._rule3_llm(prev, curr, llm_referee)
            if r3.is_continuation:
                logger.debug(
                    f"Page {curr.page_num}: continuation via LLM — {r3.reason}"
                )
                return r3
            return ContinuationVerdict(
                False,
                "rule3",
                r3.reason or "llm_rejected_or_failed",
                r3.confidence,
            )

        return ContinuationVerdict(
            False,
            "none",
            f"no_rule_matched (r1={r1.reason}; r2={r2.reason})",
            0.0,
        )

    def _soft_ly_lich_absorb(
        self,
        curr: PageSignal,
        has_open_group: bool,
        open_doc_type: Optional[str],
    ) -> ContinuationVerdict:
        """Gộp mục lục / mục số / hướng dẫn khi đang mở form lý lịch."""
        doc_key = (open_doc_type or "").upper()
        if not has_open_group or doc_key not in {
            "LY_LICH_DANG_VIEN",
            "LY_LICH_NGUOI_XIN_VAO_DANG",
        }:
            return ContinuationVerdict(False, "soft", "not_ly_lich_open", 0.0)

        if getattr(curr, "is_toc", False):
            # Mục lục hồ sơ KHÔNG thuộc form lý lịch — không absorb
            return ContinuationVerdict(
                False, "soft", "toc_never_absorb_into_ly_lich", 0.0
            )
        # Đổi khổ giấy booklet → A4: không soft-absorb
        curr_sg = getattr(curr, "page_size_group", "OTHER") or "OTHER"
        if curr_sg in {"A4_PORTRAIT", "A4_MEDIUM"}:
            return ContinuationVerdict(
                False, "soft", "a4_not_soft_absorb_into_ly_lich", 0.0
            )
        if getattr(curr, "is_form_section", False):
            return ContinuationVerdict(
                True, "soft", "form_section_inside_open_ly_lich", 0.90
            )

        blob = unidecode(
            (curr.header_text or "") + "\n" + (curr.full_text or "")[:600]
        ).lower()
        absorb_hints = (
            "so luoc ly lich",
            "huong dan",
            "nhung diem can chu y",
            "cam doan",
            "hoan canh gia dinh",
            "chung nhan cua cap uy",
            "tom tat qua trinh",
            "dao tao boi duong",
            "dac diem lich su",
        )
        if any(h in blob for h in absorb_hints):
            return ContinuationVerdict(
                True, "soft", "ly_lich_section_or_guide_hint", 0.80
            )
        return ContinuationVerdict(False, "soft", "no_soft_hint", 0.0)

    # ── Rule 1 ───────────────────────────────────────────────────────────────

    def _rule1_syntactic(
        self, prev: PageSignal, curr: PageSignal
    ) -> ContinuationVerdict:
        prev_pages = self._extract_page_fraction(prev.header_text + "\n" + prev.full_text)
        curr_pages = self._extract_page_fraction(curr.header_text + "\n" + curr.full_text)
        if prev_pages and curr_pages:
            p_cur, p_total = prev_pages
            c_cur, c_total = curr_pages
            if p_total == c_total and c_cur == p_cur + 1:
                return ContinuationVerdict(
                    True,
                    "rule1",
                    f"page_fraction {p_cur}/{p_total} → {c_cur}/{c_total}",
                    0.95,
                )

        # Continuation pattern đã gắn sẵn trên signal (Trang X/Y, tiếp theo…)
        if curr.is_continuation and not curr.has_doc_keyword:
            return ContinuationVerdict(
                True, "rule1", "continuation_pattern_on_current", 0.85
            )

        # Prev kết thúc câu dở + curr trông như giữa bài (không đủ để merge một mình —
        # cần thêm dấu hiệu mid-document trên curr để tránh nuốt trang lộn xộn).
        prev_tail = self._tail_text(prev.full_text, n_chars=220)
        if (
            prev_tail
            and not self._looks_like_signature_zone(prev_tail)
            and not curr.has_doc_keyword
        ):
            last_line = (
                prev_tail.strip().splitlines()[-1].strip() if prev_tail.strip() else ""
            )
            if last_line and not _SENTENCE_END_RE.search(last_line):
                if self._looks_like_mid_document(curr):
                    return ContinuationVerdict(
                        True,
                        "rule1",
                        "prev_incomplete_and_curr_mid_document",
                        0.72,
                    )

        return ContinuationVerdict(False, "rule1", "no_syntactic_link", 0.0)

    # ── Rule 2 ───────────────────────────────────────────────────────────────

    def _rule2_layout(
        self,
        prev: PageSignal,
        curr: PageSignal,
        has_open_group: bool = False,
        open_doc_type: Optional[str] = None,
    ) -> ContinuationVerdict:
        """
        Heuristic layout:
          A) prev cắt sát đáy + curr đầu trang dạng ô/cột
          B) (mềm) group form nhiều trang đang mở + curr dạng lưới/ô, không catalog
        """
        if curr.has_doc_keyword:
            return ContinuationVerdict(False, "rule2", "curr_has_keyword", 0.0)

        prev_blocks = getattr(prev, "header_blocks", None) or []
        prev_all = getattr(prev, "all_blocks", None) or prev_blocks
        curr_head_blocks = getattr(curr, "header_blocks", None) or []
        curr_all = getattr(curr, "all_blocks", None) or curr_head_blocks

        if not curr_head_blocks and not curr_all:
            return ContinuationVerdict(False, "rule2", "insufficient_blocks", 0.0)

        top_blocks = [
            b for b in (curr_head_blocks or curr_all)
            if getattr(b, "center_y", 1.0) <= 0.22
        ]
        short_cells = [
            b for b in top_blocks
            if len((getattr(b, "text", "") or "").strip()) <= 40
        ]
        form_like_top = len(short_cells) >= 3

        # A) Bottom-cut continuity
        if prev_all:
            near_bottom = [
                b for b in prev_all
                if getattr(b, "center_y", 0.0) >= 0.78
            ]
            if near_bottom and form_like_top:
                return ContinuationVerdict(
                    True,
                    "rule2",
                    f"table_like_top_cells={len(short_cells)} after_bottom_cut",
                    0.78,
                )

        # B) Soft form continuation — chỉ khi đang có group form nhiều trang
        doc_key = (open_doc_type or "").upper()
        if (
            has_open_group
            and doc_key in MULTI_PAGE_FORM_TYPES
            and form_like_top
        ):
            dens_ok = True
            if prev.text_density > 0 and curr.text_density > 0:
                dens_ok = abs(prev.text_density - curr.text_density) < 0.45
            if dens_ok and curr.text_density >= 0.02:
                return ContinuationVerdict(
                    True,
                    "rule2",
                    f"open_form_grid cont type={doc_key} cells={len(short_cells)}",
                    0.68,
                )

        # C) Bare page number at bottom of curr (1, 2, 3...) while group open
        if has_open_group and doc_key in MULTI_PAGE_FORM_TYPES:
            if self._has_bare_page_marker(curr):
                return ContinuationVerdict(
                    True,
                    "rule2",
                    "open_form_with_bare_page_marker",
                    0.70,
                )

        if not prev_all:
            return ContinuationVerdict(False, "rule2", "insufficient_blocks", 0.0)
        if not any(getattr(b, "center_y", 0.0) >= 0.78 for b in prev_all):
            return ContinuationVerdict(False, "rule2", "prev_not_cut_at_bottom", 0.0)
        return ContinuationVerdict(False, "rule2", "no_layout_continuity", 0.0)

    # ── Rule 3 ───────────────────────────────────────────────────────────────

    def _rule3_llm(
        self,
        prev: PageSignal,
        curr: PageSignal,
        llm_referee: Any,
    ) -> ContinuationVerdict:
        if not getattr(llm_referee, "is_available", False):
            return ContinuationVerdict(False, "rule3", "llm_unavailable", 0.0)
        if getattr(llm_referee, "_circuit_open", False):
            # Vẫn cho circuit check thử lại
            check = getattr(llm_referee, "_circuit_check", None)
            if callable(check) and not check():
                return ContinuationVerdict(False, "rule3", "llm_circuit_open", 0.0)

        prev_tail = self._tail_text(prev.full_text or prev.header_text, 350)
        curr_head = (curr.header_text or curr.full_text or "")[:350]
        if not prev_tail.strip() or not curr_head.strip():
            return ContinuationVerdict(False, "rule3", "empty_text_for_llm", 0.0)

        endpoint = getattr(llm_referee, "endpoint", config.OLLAMA_ENDPOINT)
        model = getattr(llm_referee, "model", config.OLLAMA_MODEL)
        timeout = getattr(llm_referee, "timeout", config.OLLAMA_TIMEOUT)

        payload = {
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _CONT_SYSTEM},
                {
                    "role": "user",
                    "content": _CONT_USER.format(
                        prev_tail=prev_tail, curr_head=curr_head
                    ),
                },
            ],
            "options": {"temperature": 0.0, "num_ctx": 512, "num_predict": 60},
        }

        try:
            res = requests.post(endpoint, json=payload, timeout=timeout)
            res.raise_for_status()
            data = res.json()
            raw = data.get("message", {}).get("content", "{}")
            parsed = self._parse_json(raw)
            if parsed is None:
                if hasattr(llm_referee, "_record_timeout"):
                    pass
                return ContinuationVerdict(False, "rule3", "llm_parse_fail", 0.0)

            if hasattr(llm_referee, "_record_success"):
                llm_referee._record_success()

            is_cont = bool(parsed.get("is_continuation", False))
            try:
                conf = float(parsed.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            reason = str(parsed.get("reasoning", "llm"))

            if is_cont and conf >= self.llm_min_confidence:
                return ContinuationVerdict(True, "rule3", reason, conf)
            return ContinuationVerdict(
                False,
                "rule3",
                f"llm_reject conf={conf:.2f}: {reason}",
                conf,
            )

        except requests.exceptions.Timeout:
            if hasattr(llm_referee, "_record_timeout"):
                llm_referee._record_timeout(curr.page_num)
            logger.warning(
                f"Page {curr.page_num}: continuation LLM timeout → ORPHAN"
            )
            return ContinuationVerdict(False, "rule3", "llm_timeout", 0.0)
        except Exception as exc:
            logger.warning(
                f"Page {curr.page_num}: continuation LLM error: {exc} → ORPHAN"
            )
            return ContinuationVerdict(False, "rule3", f"llm_error:{exc}", 0.0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_page_fraction(text: str) -> Optional[tuple[int, int]]:
        if not text:
            return None
        m = _PAGE_NUM_RE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None

    @staticmethod
    def _tail_text(text: str, n_chars: int = 200) -> str:
        t = (text or "").strip()
        if len(t) <= n_chars:
            return t
        return t[-n_chars:]

    @staticmethod
    def _has_bare_page_marker(curr: PageSignal) -> bool:
        """Số trang in kiểu '- 2 -' hoặc dòng chỉ có số ở cuối/header."""
        text = (curr.header_text or "") + "\n" + (curr.full_text or "")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # Chỉ xét vài dòng cuối + đầu
        candidates = lines[:3] + lines[-3:]
        for ln in candidates:
            if _BARE_PAGE_RE.match(ln):
                try:
                    n = int(re.sub(r"\D", "", ln))
                    if 1 <= n <= 80:
                        return True
                except ValueError:
                    pass
            if _PAGE_NUM_RE.search(ln):
                return True
        return False

    @staticmethod
    def _looks_like_signature_zone(text: str) -> bool:
        ascii_t = unidecode(text).lower()
        return any(h in ascii_t for h in _SIGNATURE_HINTS)

    @staticmethod
    def _looks_like_mid_document(curr: PageSignal) -> bool:
        """
        Curr giống phần giữa tài liệu.
        Chỉ dùng hint mạnh — tránh gộp trang lộn xộn chỉ vì không viết hoa.
        """
        if curr.is_continuation:
            return True
        header = (curr.header_text or "").strip()
        full = (curr.full_text or header).strip()
        if not full:
            return False
        sample = unidecode(full[:220]).lower()
        mid_hints = (
            "dieu ",
            "muc ",
            "khoan ",
            "tiep theo",
            "trang ",
            "page ",
        )
        return any(h in sample for h in mid_hints)

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            import json

            return json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return None
            try:
                import json

                return json.loads(m.group(0))
            except Exception:
                return None
