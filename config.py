# config.py — All tunable constants. Edit here, not in individual modules.

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = PROJECT_ROOT / "models"

# ── PDF Rendering ──────────────────────────────────────────────────
PDF_RENDER_DPI = 200  # Higher = better OCR, slower. 150-300 range.

# ── Image Preprocessing ────────────────────────────────────────────
MAX_SKEW_ANGLE = 10.0  # Max degrees to search for deskew
SKEW_THRESHOLD = 1.5  # Only correct if skew > this (degrees)
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_SIZE = (8, 8)

# ── OCR ────────────────────────────────────────────────────────────
OCR_LANG = "vi"
OCR_USE_GPU = False
OCR_MIN_CONFIDENCE = 0.40  # Discard blocks below this confidence

# ── Signal Extraction ──────────────────────────────────────────────
HEADER_ZONE_TOP = 0.00  # Top of header zone (normalized)
HEADER_ZONE_BOTTOM = 0.33  # Bottom of header zone (normalized, = 1/3 of page)
KEYWORD_FUZZY_THRESHOLD = 85  # rapidfuzz partial_ratio threshold
LARGE_FONT_HEIGHT_RATIO = 0.03  # bbox_height / image_height to be "large font"
CENTER_TEXT_X_RANGE = (0.30, 0.70)  # horizontal range for "centered" text

# ── Boundary Detection (3-state anti-swallow) ──────────────────────
BOUNDARY_THRESHOLD = 0.45  # soft score (legacy / logging)
HIGH_BOUNDARY_THRESHOLD = 0.70  # score >= this → NEW even without catalog hit
LOW_CONFIDENCE_RANGE = (0.35, 0.55)
BLANK_PAGE_DENSITY = 0.05  # text_density < this → SKIP_BLANK
HEADER_SIMILARITY_THRESHOLD = 0.85  # token_sort_ratio above this → same doc
CATALOG_FUZZY_THRESHOLD = 82  # party_doc_matcher partial_ratio
CATALOG_MATCH_MIN_SCORE = 82  # min score to treat as NEW via catalog

# ── Scoring Weights ────────────────────────────────────────────────
SCORE_HAS_KEYWORD = +0.45
SCORE_HAS_LARGE_CENTERED = +0.25
SCORE_DENSITY_CHANGE = +0.10
SCORE_AFTER_SEPARATOR = +0.30
SCORE_IS_CONTINUATION = -0.55
SCORE_HEADER_SIMILAR = -0.30
SCORE_LOW_OCR_CONFIDENCE = -0.20
SCORE_SIZE_GROUP_CHANGE = +0.50  # hard boundary khi đổi kích thước vật lý
SCORE_PREV_END_OF_DOC = +0.35    # trang trước có tín hiệu kết thúc tài liệu

# ── Page size groups (PyMuPDF page.rect, đơn vị pt) ─────────────────
# Tín hiệu gold từ PaperStream scan — không phụ thuộc OCR.
PAGE_SIZE_GROUPS: dict = {
    "BOOKLET_SMALL": {
        "width_range": (370, 400),
        "height_range": (560, 600),
        "doc_type_hint": "LY_LICH_DANG_VIEN",
        "ocr_dpi": 300,
        "continuation_bias": 0.65,
    },
    "LANDSCAPE_SMALL": {
        "width_range": (510, 545),
        "height_range": (395, 415),
        "doc_type_hint": "LY_LICH_DANG_VIEN",  # sơ yếu → LY_LICH (không invent key)
        "ocr_dpi": 250,
        "continuation_bias": 0.55,
    },
    "A4_PORTRAIT": {
        "width_range": (570, 610),
        "height_range": (835, 860),
        "doc_type_hint": None,
        "ocr_dpi": 200,
        "continuation_bias": 0.0,
    },
    "A4_MEDIUM": {
        "width_range": (560, 615),
        "height_range": (800, 835),
        "doc_type_hint": None,
        "ocr_dpi": 200,
        "continuation_bias": 0.10,
    },
    "OTHER": {
        "width_range": None,
        "height_range": None,
        "doc_type_hint": None,
        "ocr_dpi": 200,
        "continuation_bias": 0.0,
    },
}

# Size groups đủ đặc trưng để soft-continuation / Pass-2 reattach
STRONG_SIZE_CONTINUATION_GROUPS = frozenset({"BOOKLET_SMALL", "LANDSCAPE_SMALL"})

# Pass-2 orphan reattach
REATTACH_HARD_MIN_CONFIDENCE = 0.80   # gắn cứng vào group success
REATTACH_TENTATIVE_MIN_CONFIDENCE = 0.65  # attach nhưng → _review/tentative/

# End-of-document
EOD_BOTTOM_THRESHOLD = 0.72
EOD_MIN_CONFIDENCE = 0.65


def classify_page_size(width_pt: float, height_pt: float) -> str:
    """Phân loại kích thước trang vật lý (pt) → PAGE_SIZE_GROUPS key."""
    for group_name, cfg in PAGE_SIZE_GROUPS.items():
        if group_name == "OTHER":
            continue
        wr = cfg.get("width_range")
        hr = cfg.get("height_range")
        if wr is None or hr is None:
            continue
        w_min, w_max = wr
        h_min, h_max = hr
        if w_min <= width_pt <= w_max and h_min <= height_pt <= h_max:
            return group_name
    return "OTHER"


def page_size_ocr_dpi(width_pt: float, height_pt: float, default_dpi: int | None = None) -> int:
    """DPI OCR khuyến nghị theo size group."""
    group = classify_page_size(width_pt, height_pt)
    dpi = PAGE_SIZE_GROUPS.get(group, {}).get("ocr_dpi")
    if dpi is None:
        return int(default_dpi or PDF_RENDER_DPI)
    return int(dpi)

# ── Continuation validator ─────────────────────────────────────────
ENABLE_CONTINUATION_LLM = False  # Bật bằng --enable-continuation-llm (cần Ollama)
CONTINUATION_LLM_MIN_CONFIDENCE = 0.75

# ── Output ─────────────────────────────────────────────────────────
MAX_SLUG_LENGTH = 60
PDF_COMPRESS_LEVEL = 4  # pymupdf garbage collection level (0-4)

# ── Memory ─────────────────────────────────────────────────────────
GC_EVERY_N_PAGES = 100
LOG_PROGRESS_EVERY = 50

# ── Handwriting Support ────────────────────────────────────────────
ENABLE_HANDWRITING_OCR = False  # Tắt mặc định — bật khi cần TrOCR
TROCR_MODEL_NAME = "microsoft/trocr-base-handwritten"
TROCR_CACHE_DIR = str(MODELS_DIR / "trocr")
TROCR_BATCH_SIZE = 4  # Số crops xử lý cùng lúc
TROCR_USE_GPU = False
HW_STROKE_THRESHOLD = 0.35  # Irregularity score để phân biệt viết tay
HW_MIN_REGION_AREA = 500  # px² — bỏ qua vùng nhỏ hơn

# ── Local LLM Referee (Ollama Hybrid) ──────────────────────────────
ENABLE_LLM_REFEREE = False       # Không override boundary kiểu cũ; LLM chỉ qua ContinuationValidator
OLLAMA_ENDPOINT = "http://localhost:11434/api/chat"  # /api/chat (không phải /api/generate)
OLLAMA_MODEL = "qwen2.5:3b"      # Model nhẹ, chạy mượt trên CPU/GPU
OLLAMA_TIMEOUT = 10.0            # Giới hạn thời gian chờ (giây)
LLM_REVIEW_RANGE = (0.28, 0.62)  # Những trang có điểm trong khoảng này sẽ chuyển cho LLM phán quyết

# ── Year-Aware Sequencing ───────────────────────────────────────────
YEAR_EXTRACT_OCR_FIXUP  = True   # True = sửa ký tự nhầm O→0, l→1, I→1 trước khi extract
YEAR_MIN_VALID          = 1945   # Năm tối thiểu hợp lệ (lịch sử Đảng)
YEAR_MAX_VALID          = 2035   # Năm tối đa hợp lệ
YEAR_SENTINEL_NO_YEAR   = 9999   # Sentinel: tài liệu không có năm → xếp cuối nhóm

# ── LLM Circuit Breaker ─────────────────────────────────────────────
LLM_CIRCUIT_OPEN_AFTER  = 3     # Số timeout liên tiếp trước khi ngắt mạch
LLM_CIRCUIT_RESET_AFTER = 50    # Số trang bỏ qua trước khi thử kết nối lại