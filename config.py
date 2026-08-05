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