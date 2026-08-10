"""
pipeline/identity_extractor.py
==============================
Trích họ tên + CCCD/CMND (+ mã cấp ủy nếu có) từ OCR phiếu ĐV / mục lục / lý lịch
để dựng đường dẫn Phụ lục 2 khi thiếu CLI.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from loguru import logger
from unidecode import unidecode


@dataclass
class MemberIdentity:
    ho_ten: Optional[str] = None
    cccd: Optional[str] = None
    tdv: Optional[str] = None  # Số thẻ ĐV — fallback folder key khi thiếu CCCD
    m1: Optional[str] = None
    m2: Optional[str] = None
    m3: Optional[str] = None
    m4: Optional[str] = None
    m5: Optional[str] = None
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)

    @property
    def folder_id(self) -> Optional[str]:
        """CCCD ưu tiên; TĐV nếu config cho phép."""
        if self.cccd:
            return self.cccd
        if self.tdv and getattr(
            __import__("config"), "IDENTITY_ALLOW_TDV_AS_FOLDER_KEY", True
        ):
            return self.tdv
        return None

    @property
    def has_member_folder_keys(self) -> bool:
        return bool(self.ho_ten and self.folder_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Họ tên: ưu tiên nhãn form phiếu / mục lục (match trên ASCII unidecode)
_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ho\s*(?:va\s*)?ten\s*(?:\([^)]{0,48}\))?\s*(?:khai\s*sinh)?\s*[:\.]?\s*"
        r"([A-Za-z][A-Za-z\s\.]{3,50}?)(?="
        r"\s*(?:sinh\s*ngay|ngay\s*sinh|ten\s*goi|bi\s*danh|so\s*tdv|so\s*cccd|"
        r"dia\s*chi|chuc\s*vu|nam\s+\d{4}|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:cua\s+)?dong\s*chi\s*[:\.]?\s*"
        r"([A-Za-z][A-Za-z\s\.]{3,40}?)(?=\s*(?:sinh|vao\s*dang|bi\s*danh|\n|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"ho\s*so\s*dang\s*vien\s*(?:cua\s*)?(?:dong\s*chi\s*)?[:\.]?\s*"
        r"([A-Za-z][A-Za-z\s\.]{3,40})",
        re.IGNORECASE,
    ),
    # Dòng họ tên in hoa đứng riêng: PHAM HUU LUAT
    re.compile(
        r"(?:^|\n)\s*([A-Z]{2,}(?:\s+[A-Z]{2,}){1,4})\s*(?:\n|$)",
    ),
]

# Sửa OCR méo họ tên phổ biến trong hồ sơ này / hồ sơ đảng
_OCR_NAME_FIXUPS = {
    "phm": "Pham",
    "pham": "Pham",
    "hs": "Huu",
    "hu": "Huu",
    "hy": "Huy",
    "lut": "Luat",
    "loit": "Luat",
    "lec ar": "Luat",
    "lecar": "Luat",
}

_CCCD_LABELED = re.compile(
    r"(?:so\s*)?(?:cccd|cccd\/cmnd|cmnd|can\s*cuoc(?:\s*cong\s*dan)?|"
    r"giay\s*cmnd|so\s*cmnd)\s*[:\.]?\s*([0-9OIl]{8,14})",
    re.IGNORECASE,
)
# Chỉ dùng bare 12 số (CCCD mới). Không lấy bare 9 số — dễ nhầm số TĐV.
_CCCD_BARE_12 = re.compile(r"\b([0-9]{12})\b")

# Số thẻ đảng viên / TĐV — không được coi là CCCD
_TDV_NEAR = re.compile(
    r"(?:so\s*)?(?:tdv|the\s*dang(?:\s*vien)?|so\s*the)\s*[:\.]?\s*"
    r"[0-9OIl]{6,14}",
    re.IGNORECASE,
)
_TDV_DIGITS = re.compile(
    r"(?:so\s*)?(?:tdv|the\s*dang(?:\s*vien)?|so\s*the)\s*[:\.]?\s*"
    r"([0-9OIl]{6,14})",
    re.IGNORECASE,
)

# Mã cấp ủy dạng 93.015.000.001.002 hoặc 93.000.036.001.015
_M_CODES_RE = re.compile(
    r"\b(\d{1,2})\s*[.\-]\s*(\d{1,3})\s*[.\-]\s*(\d{1,3})\s*[.\-]\s*"
    r"(\d{1,3})\s*[.\-]\s*(\d{1,3})\b"
)

_NOISE_NAME = frozenset(
    {
        "dang",
        "cong",
        "san",
        "viet",
        "nam",
        "phieu",
        "dang vien",
        "ly lich",
        "muc luc",
        "tai lieu",
        "khong",
        "co",
        "thanh",
        "uy",
        "chi",
        "bo",
        "bi",
        "danh",
        "bi danh",
        "ten",
        "goi",
        "khac",
    }
)

# Họ VN phổ biến — tên OCR sạch thường bắt đầu bằng họ này
_COMMON_SURNAMES = frozenset(
    {
        "nguyen",
        "tran",
        "le",
        "pham",
        "hoang",
        "huynh",
        "phan",
        "vu",
        "vo",
        "dang",
        "bui",
        "do",
        "ho",
        "ngo",
        "duong",
        "ly",
        "doan",
        "truong",
        "dinh",
        "lam",
        "mai",
        "dao",
        "cao",
        "chu",
        "ha",
        "luu",
        "tong",
        "ta",
        "to",
        "tang",
        "thai",
        "van",
        "quach",
        "thach",
        "lau",
        "kieu",
        "ung",
        "ong",
        "la",
        "an",
        "au",
    }
)

_SUSPICIOUS_NAME_TOKEN = re.compile(
    r"(ack|ahang|lacain|qq+|xx+|zz+|ck$|bh$)",
    re.IGNORECASE,
)

# Nguyên âm ASCII — tên người Việt OCR sạch phải có mật độ nguyên âm hợp lý
_VOWELS = set("aeiouyAEIOUY")


def _ocr_digit_fixup(raw: str) -> str:
    return (
        (raw or "")
        .replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
    )


def _name_quality_ok(name: str) -> bool:
    """Loại tên OCR rác kiểu 'Lacain Ahang T' / 'Pham Hay Ack Bi Danh'."""
    ascii_n = unidecode(name or "")
    parts = [p for p in ascii_n.split() if p]
    if len(parts) < 2 or len(parts) > 5:
        return False
    # Họ tên VN: mỗi tiếng ≥ 2 ký tự chữ (không chấp nhận chữ cái đơn)
    for p in parts:
        letters_only = re.sub(r"[^A-Za-z]", "", p)
        if len(letters_only) < 2:
            return False
        if _SUSPICIOUS_NAME_TOKEN.search(letters_only):
            return False
    # Cấm nhãn form còn sót
    low_parts = {p.lower() for p in parts}
    if low_parts & {"bi", "danh", "alias"}:
        return False
    letters = [c for c in ascii_n if c.isalpha()]
    if len(letters) < 6:
        return False
    vowel_n = sum(1 for c in letters if c in _VOWELS)
    ratio = vowel_n / max(1, len(letters))
    if ratio < 0.28 or ratio > 0.72:
        return False
    # Quá nhiều phụ âm liên tiếp → OCR méo
    if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", ascii_n.lower()):
        return False
    # 5 tiếng mà không phải họ phổ biến → nghi OCR rác
    if len(parts) >= 5 and parts[0].lower() not in _COMMON_SURNAMES:
        return False
    return True


def _apply_ocr_name_fixups(parts: list[str]) -> list[str]:
    out: list[str] = []
    for p in parts:
        key = unidecode(p).lower()
        if key in _OCR_NAME_FIXUPS:
            out.append(_OCR_NAME_FIXUPS[key])
        else:
            out.append(p)
    return out


def _clean_person_name(raw: str) -> Optional[str]:
    if not raw:
        return None
    # Cắt tại xuống dòng / nhãn số giấy tờ
    raw = re.split(r"[\n\r]+", raw, maxsplit=1)[0]
    # OCR hay chấm giữa tiếng: "PHAM. HU. LUAT" → khoảng trắng
    name = re.sub(r"[.\u2022·]+", " ", raw)
    name = re.sub(r"[\d:;|/\\]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_,")
    # Cắt phần sau nhãn phụ
    name = re.split(
        r"\b(bi\s*danh|ten\s*goi\s*khac|ten\s*khac|alias|"
        r"gioi\s*tinh|sinh\s*ngay|ngay\s*sinh|que\s*quan|thuong\s*tru|"
        r"dan\s*toc|so\s*cccd|so\s*cmnd|cccd|cmnd|so\s*tdv|the\s*dang|"
        r"/\s*nam\b|/\s*nu\b)\b",
        name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .-_,")
    # Cắt dính "LUATSinh" → "LUAT"
    name = re.split(r"(?i)(?<=[a-z])(?=sinh)", name, maxsplit=1)[0]
    parts = [p for p in name.split() if p]
    if len(parts) < 2 or len(parts) > 6:
        return None
    parts = _apply_ocr_name_fixups(parts)
    ascii_low = unidecode(" ".join(parts)).lower()
    # Dòng ALLCAPS / OCR nhiễu kiểu CHI BO / DANG BO / mục lục
    if ascii_low in {"chi bo", "dang bo", "dang uy", "thanh uy"}:
        return None
    if any(
        x in ascii_low
        for x in (
            "hang nam",
            "khi chuyen",
            "sinh hoat",
            "phieu bo",
            "phieu dang",
            "muc luc",
            "tai lieu",
            "dang cong",
            "cong san",
            "viet nam",
            "dang vien",
        )
    ):
        return None
    if any(n in ascii_low for n in _NOISE_NAME) and len(parts) <= 2:
        return None
    cleaned = " ".join(p[:1].upper() + p[1:].lower() for p in parts)
    if len(cleaned) < 5:
        return None
    if not _name_quality_ok(cleaned):
        return None
    return cleaned


def _extract_name_from_blob(blob: str) -> Optional[str]:
    # Match trên ASCII; giữ newline cho pattern dòng in hoa
    ascii_blob = unidecode(blob or "")
    ascii_blob = re.sub(r"(?i)(luat)(sinh)", r"\1 \2", ascii_blob)
    dotted = re.sub(r"\.(?=\s*[A-Za-z])", " ", ascii_blob)
    # Bản một dòng cho pattern nhãn
    flat = re.sub(r"\s+", " ", dotted)

    for cre in _NAME_PATTERNS[:-1]:
        m = cre.search(flat)
        if not m:
            continue
        name = _clean_person_name(m.group(1))
        if name:
            return name
    # Pattern dòng ALLCAPS — cần newline + họ phổ biến
    m = _NAME_PATTERNS[-1].search(dotted)
    if m:
        cand = m.group(1)
        first = unidecode(cand.split()[0]).lower() if cand.split() else ""
        if first in _COMMON_SURNAMES:
            name = _clean_person_name(cand)
            if name:
                return name
    return None


def _extract_tdv_from_blob(blob: str) -> Optional[str]:
    ascii_blob = unidecode(blob or "")
    best: Optional[str] = None
    for m in _TDV_DIGITS.finditer(ascii_blob):
        d = re.sub(r"\D", "", _ocr_digit_fixup(m.group(1)))
        if 6 <= len(d) <= 12:
            if best is None or len(d) > len(best):
                best = d
    return best


def _tdv_digit_set(ascii_blob: str) -> set[str]:
    out: set[str] = set()
    for m in _TDV_DIGITS.finditer(ascii_blob):
        d = re.sub(r"\D", "", _ocr_digit_fixup(m.group(1)))
        if d:
            out.add(d)
            if len(d) >= 7:
                out.add(d[:7])
                out.add(d[:8])
                out.add(d[:9])
    return out


def _extract_cccd_from_blob(blob: str) -> Optional[str]:
    ascii_blob = unidecode(blob)
    tdv_nums = _tdv_digit_set(ascii_blob)

    def _ok_vs_tdv(digits: str) -> bool:
        if digits in tdv_nums:
            return False
        return not any(
            digits.startswith(t) or t.startswith(digits)
            for t in tdv_nums
            if len(t) >= 7
        )

    m = _CCCD_LABELED.search(ascii_blob)
    if m:
        digits = re.sub(r"\D", "", _ocr_digit_fixup(m.group(1)))
        if len(digits) in (9, 12) and _ok_vs_tdv(digits):
            return digits

    compact = re.sub(r"\s+", "", ascii_blob)
    for m in _CCCD_BARE_12.finditer(compact):
        d = m.group(1)
        if d.startswith(("19", "20")):
            continue
        if not _ok_vs_tdv(d):
            continue
        start = max(0, m.start() - 24)
        window = compact[start : m.end() + 8].lower()
        if "tdv" in window or "thedang" in window or "sothe" in window:
            continue
        return d
    return None


def _extract_m_codes(blob: str) -> Optional[tuple[str, str, str, str, str]]:
    ascii_blob = unidecode(blob)
    m = _M_CODES_RE.search(ascii_blob)
    if not m:
        return None
    return tuple(str(int(g)) for g in m.groups())  # type: ignore[return-value]


def extract_member_identity_from_text(
    text: str,
    *,
    source: str = "ocr",
) -> MemberIdentity:
    """Trích identity từ một khối OCR."""
    ident = MemberIdentity()
    if not text or not text.strip():
        return ident

    blob = text[:4000]
    name = _extract_name_from_blob(blob)
    if name:
        ident.ho_ten = name
        ident.sources.append(f"ho_ten:{source}")
        ident.confidence += 0.45

    cccd = _extract_cccd_from_blob(blob)
    if cccd:
        ident.cccd = cccd
        ident.sources.append(f"cccd:{source}")
        ident.confidence += 0.45

    tdv = _extract_tdv_from_blob(blob)
    if tdv:
        ident.tdv = tdv
        ident.sources.append(f"tdv:{source}")
        if not cccd:
            ident.confidence += 0.25

    m_codes = _extract_m_codes(blob)
    if m_codes:
        ident.m1, ident.m2, ident.m3, ident.m4, ident.m5 = m_codes
        ident.sources.append(f"m_codes:{source}")
        ident.confidence += 0.15

    ident.confidence = min(1.0, ident.confidence)
    return ident


def merge_identities(*parts: MemberIdentity) -> MemberIdentity:
    """Gộp nhiều kết quả; ưu tiên tên đi kèm CCCD/TĐV / chất lượng cao hơn."""
    out = MemberIdentity()
    best_name: Optional[str] = None
    best_name_score = -1.0
    for p in parts:
        if not p:
            continue
        if p.ho_ten and _name_quality_ok(p.ho_ten):
            score = float(p.confidence)
            if p.cccd:
                score += 2.0
            elif p.tdv:
                score += 1.0
            if score > best_name_score:
                best_name = p.ho_ten
                best_name_score = score
        if out.cccd is None and p.cccd:
            out.cccd = p.cccd
        if out.tdv is None and p.tdv:
            out.tdv = p.tdv
        for attr in ("m1", "m2", "m3", "m4", "m5"):
            if getattr(out, attr) is None and getattr(p, attr) is not None:
                setattr(out, attr, getattr(p, attr))
        out.sources.extend(p.sources)
        out.confidence = max(out.confidence, p.confidence)
    out.ho_ten = best_name
    score = 0.0
    if out.ho_ten:
        score += 0.45
    if out.cccd:
        score += 0.45
    elif out.tdv:
        score += 0.25
    if out.m1 is not None:
        score += 0.10
    out.confidence = min(1.0, max(out.confidence, score))
    return out


def extract_member_identity_from_signals(
    signals: dict[int, Any],
    *,
    prefer_doc_types: Iterable[str] | None = None,
) -> MemberIdentity:
    """
    Quét PageSignal theo ưu tiên: Phiếu ĐV → Mục lục → Lý lịch → còn lại.
    """
    prefer = {
        (t or "").upper()
        for t in (
            prefer_doc_types
            or (
                "PHIEU_DANG_VIEN",
                "PHIEU_BO_SUNG_HO_SO_DANG_VIEN",
                "LY_LICH_DANG_VIEN",
                "LY_LICH_NGUOI_XIN_VAO_DANG",
            )
        )
    }

    def _rank(pn: int, sig: Any) -> tuple[int, int]:
        dtype = (getattr(sig, "matched_doc_type", "") or "").upper()
        if getattr(sig, "is_toc", False):
            tier = 1
        elif dtype in prefer and "PHIEU" in dtype:
            tier = 0
        elif dtype in prefer:
            tier = 2
        else:
            tier = 3
        return (tier, pn)

    parts: list[MemberIdentity] = []
    for pn, sig in sorted(signals.items(), key=lambda kv: _rank(kv[0], kv[1])):
        header = getattr(sig, "header_text", "") or ""
        full = getattr(sig, "full_text", "") or ""
        blob = header + "\n" + full
        if len(blob.strip()) < 10:
            continue
        dtype = (getattr(sig, "matched_doc_type", "") or "") or (
            "toc" if getattr(sig, "is_toc", False) else f"page{pn}"
        )
        part = extract_member_identity_from_text(blob, source=f"{dtype}@{pn}")
        if part.ho_ten or part.cccd or part.tdv or part.m1 is not None:
            parts.append(part)
            logger.debug(
                f"[identity] page {pn}: name={part.ho_ten!r} "
                f"cccd={part.cccd!r} tdv={part.tdv!r}"
            )
        # Đủ họ tên + (CCCD|TĐV) thì dừng sớm
        merged = merge_identities(*parts)
        if merged.has_member_folder_keys:
            logger.info(
                f"[identity] extracted ho_ten={merged.ho_ten!r} "
                f"cccd={merged.cccd!r} tdv={merged.tdv!r} "
                f"folder_id={merged.folder_id!r} conf={merged.confidence:.2f}"
            )
            return merged

    merged = merge_identities(*parts)
    if merged.ho_ten or merged.cccd or merged.tdv:
        logger.info(
            f"[identity] partial ho_ten={merged.ho_ten!r} "
            f"cccd={merged.cccd!r} tdv={merged.tdv!r} conf={merged.confidence:.2f}"
        )
    else:
        logger.warning("[identity] không trích được họ tên/CCCD/TĐV từ OCR")
    return merged


def apply_cli_overrides(
    ocr_ident: MemberIdentity,
    *,
    ho_ten: Optional[str] = None,
    cccd: Optional[str] = None,
    m1: Optional[str] = None,
    m2: Optional[str] = None,
    m3: Optional[str] = None,
    m4: Optional[str] = None,
    m5: Optional[str] = None,
) -> MemberIdentity:
    """CLI thắng OCR khi được cung cấp."""
    out = MemberIdentity(
        ho_ten=ocr_ident.ho_ten,
        cccd=ocr_ident.cccd,
        tdv=ocr_ident.tdv,
        m1=ocr_ident.m1,
        m2=ocr_ident.m2,
        m3=ocr_ident.m3,
        m4=ocr_ident.m4,
        m5=ocr_ident.m5,
        confidence=ocr_ident.confidence,
        sources=list(ocr_ident.sources),
    )
    if ho_ten:
        out.ho_ten = ho_ten.strip()
        out.sources.append("ho_ten:cli")
    if cccd:
        digits = re.sub(r"\D", "", cccd)
        if digits:
            out.cccd = digits
            out.sources.append("cccd:cli")
    for attr, val in (
        ("m1", m1),
        ("m2", m2),
        ("m3", m3),
        ("m4", m4),
        ("m5", m5),
    ):
        if val is not None and str(val).strip() != "":
            setattr(out, attr, str(val).strip())
            out.sources.append(f"{attr}:cli")
    if out.has_member_folder_keys:
        out.confidence = max(out.confidence, 0.9 if ho_ten and cccd else 0.7)
    return out
