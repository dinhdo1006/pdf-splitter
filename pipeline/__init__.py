"""Vietnamese PDF Auto-Splitter pipeline modules."""

# Imports are intentionaly lazy — import concrete modules directly:
#   from pipeline.ingestor import PDFIngestor
#   from pipeline.boundary_detector import BoundaryDetector
# etc.
# This avoids pulling PaddleOCR / OpenCV when running a single module's smoke test.

__all__ = [
    # ── Pipeline cốt lõi ─────────────────────────────────────
    "PDFIngestor",
    "PagePreprocessor",
    "OCREngine",
    "OCRBlock",
    "SignalExtractor",
    "PageSignal",
    "BoundaryDetector",
    "BoundaryDecision",
    "DocumentGroup",
    "PageClass",
    "TitleNormalizer",
    "PDFExporter",
    # ── Chuẩn hóa đầu ra hồ sơ đảng viên (1361-CV/BTCTW) ────
    "PARTY_DOC_CATALOG",
    "fmt_stt",
    "lookup_by_key",
    "lookup_by_stt",
    "priority_from_stt",
    "PartyPathBuilder",
    "build_member_dir",
    "PartyFilenameResolver",
    "ResolvedFilename",
    "build_filename",
    "PartyOutputNormalizer",
    "NormalizedOutputItem",
    "normalize_member_output",
]
