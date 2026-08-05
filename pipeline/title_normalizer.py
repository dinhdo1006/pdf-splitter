"""Module 6: Title extraction + filesystem-safe file naming."""

from __future__ import annotations

import re
import unicodedata

from unidecode import unidecode

import config
from pipeline.signal_extractor import CONTINUATION_PATTERNS, DOCUMENT_KEYWORDS


class TitleNormalizer:
    """Convert raw OCR header text into clean, unique PDF filenames."""

    def __init__(self) -> None:
        self._seen_slugs: dict[str, int] = {}  # slug → count

    def extract_title(self, raw_header_text: str) -> str:
        """
        Extract the best document title from raw header OCR text.
        Score lines by keyword presence, casing, length; penalize page numbers.
        """
        if not raw_header_text or not raw_header_text.strip():
            return "UNTITLED"

        lines = [ln.strip() for ln in raw_header_text.splitlines() if ln.strip()]
        if not lines:
            return "UNTITLED"

        best_line = lines[0]
        best_score = float("-inf")

        for line in lines:
            score = 0
            line_upper = line.upper()
            line_ascii = unidecode(line_upper).upper()

            for keyword in DOCUMENT_KEYWORDS:
                kw = keyword.upper()
                kw_ascii = unidecode(kw).upper()
                if kw in line_upper or kw_ascii in line_ascii:
                    score += 3
                    break

            # ALL CAPS or Title Case
            letters = [c for c in line if c.isalpha()]
            if letters and all(c.isupper() for c in letters):
                score += 2
            elif line == line.title() and any(c.isalpha() for c in line):
                score += 2

            if 5 <= len(line) <= 80:
                score += 1

            # Penalize pure numeric / continuation patterns
            if line.isdigit() or re.fullmatch(r"[\d\s./\-]+", line):
                score -= 2
            for pattern in CONTINUATION_PATTERNS:
                if re.search(pattern, line, flags=re.IGNORECASE):
                    score -= 2
                    break

            if score > best_score:
                best_score = score
                best_line = line

        if best_score <= 0:
            for line in lines:
                if line.strip():
                    return line.strip()
            return "UNTITLED"

        return best_line.strip()

    def to_slug(self, title: str) -> str:
        """
        Convert Vietnamese title to ASCII filesystem-safe slug.
        e.g. "Hợp Đồng Lao Động" → "HOP_DONG_LAO_DONG"
        """
        if not title or not title.strip():
            return "UNTITLED"

        normalized = unicodedata.normalize("NFC", title)
        ascii_text = unidecode(normalized).upper()
        slug = re.sub(r"[^A-Z0-9]+", "_", ascii_text)
        slug = re.sub(r"_+", "_", slug)
        slug = slug.strip("_")
        if not slug:
            slug = "UNTITLED"
        return slug[: config.MAX_SLUG_LENGTH]

    def unique_filename(self, title: str) -> str:
        """
        Generate a unique filename (without .pdf extension).
        e.g. "HOP_DONG_LAO_DONG_01"
        """
        extracted = self.extract_title(title)
        slug = self.to_slug(extracted)
        self._seen_slugs[slug] = self._seen_slugs.get(slug, 0) + 1
        count = self._seen_slugs[slug]
        return f"{slug}_{count:02d}"

    def reset(self) -> None:
        """Clear seen slugs counter (for reuse across pipeline runs)."""
        self._seen_slugs.clear()


# Quick test
if __name__ == "__main__":
    import sys

    # Avoid Windows console encoding crashes on Vietnamese text
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    n = TitleNormalizer()

    samples = [
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nHỢP ĐỒNG LAO ĐỘNG\nSố: 01/2024",
        "HÓA ĐƠN GIÁ TRỊ GIA TĂNG",
        "Trang 2/5",
        "BÁO CÁO TÀI CHÍNH NĂM 2024",
        "HỢP ĐỒNG LAO ĐỘNG",  # duplicate title → _02
        "",
    ]

    for s in samples:
        title = n.extract_title(s)
        slug = n.to_slug(title)
        fname = n.unique_filename(s)
        print(f"title={title!r}")
        print(f"  slug={slug!r}  file={fname}.pdf")
        print()
