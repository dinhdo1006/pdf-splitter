# Vietnamese PDF Auto-Splitter (Hồ sơ đảng viên — 3-state + Pass-2)

Tách PDF scan hồ sơ đảng viên hỗn hợp theo catalog Phụ lục 1361 (104 loại),
chống nuốt tài liệu (NEW / CONFIRMED_CONTINUATION / ORPHAN), Pass-2 reattach,
đặt tên theo Phụ lục 2.

Repo: https://github.com/dinhdo1006/pdf-splitter

## Installation (Linux / server GPU)

```bash
cd ~/Downloads
git clone https://github.com/dinhdo1006/pdf-splitter.git
cd pdf-splitter

sudo apt install -y python3-venv python3-full
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

> **GPU:** mặc định `OCR_USE_GPU = "auto"` — có CUDA + đủ VRAM thì GPU, không thì CPU.
> Ép tay: `python main.py ... --gpu` hoặc `--cpu`.

## First run

```bash
# Test nhanh
python main.py -i path/to/hoso.pdf -o ./output --pages 10 --dpi 200

# Full + adaptive DPI theo kích thước trang
python main.py -i path/to/hoso.pdf -o ./output --dpi 200 --adaptive-dpi

# Cây thư mục Phụ lục 2 (CLI — ưu tiên cao nhất)
python main.py -i hoso.pdf -o ./output \
  --m1 93 --m2 0 --m3 36 --m4 1 --m5 15 \
  --cccd 012345678901 --ho-ten "Nguyen Van A"

# Không truyền CLI: hệ thống cố trích họ tên + CCCD từ OCR phiếu/mục lục
# → tạo output/00.000.000.000.000/<CCCD>_<HoTen>/ (M1–M5 mặc định nếu thiếu)
```

## How it works

```
Ingest (pt size) → Preprocess → OCR → Signal (+ page_size_group, EOD)
  → BoundaryDetector 3-state (+ soft booklet cont, size change, prev EOD)
  → Pass-2 OrphanReattacher (attach_prev only)
  → YearAwareSequencer (năm từ full OCR blob → .N)
  → IdentityExtractor (họ tên/CCCD từ OCR, CLI override)
  → Export STT.Ten[.N].pdf vào member_dir Phụ lục 2 khi đủ identity
  → orphan review: MUC_LUC_/BIEN_BAN_/ORPHAN_page_XXXX.pdf
  → manifest.json + member_dir/manifest_ho_so.json
```

**Page size groups (gold signal từ `page.rect` pt):**
- `BOOKLET_SMALL` (~386×572) → lý lịch booklet, soft continuation mạnh
- `LANDSCAPE_SMALL` (~528×405) → sơ yếu lý lịch
- `A4_PORTRAIT` / `A4_MEDIUM` → tài liệu hiện đại

**Constraints:** không auto-merge thiếu evidence; không `attach_next`;
reattach conf &lt; 0.80 → `_review/tentative/`.

## Configuration (`config.py`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `PDF_RENDER_DPI` | `200` | DPI mặc định |
| `HIGH_BOUNDARY_THRESHOLD` | `0.70` | NEW không cần catalog |
| `BOUNDARY_THRESHOLD` | `0.45` | Soft / bootstrap trang 1 |
| `CATALOG_MATCH_MIN_SCORE` | `82` | Ngưỡng matcher |
| `SCORE_SIZE_GROUP_CHANGE` | `+0.50` | Đổi kích thước trang |
| `SCORE_PREV_END_OF_DOC` | `+0.35` | Trang trước có chữ ký/dấu |
| `ENABLE_CONTINUATION_LLM` | `False` | Rule 3 Ollama |
| `ENABLE_HANDWRITING_OCR` | `False` | TrOCR (opt-in) |
| `PAGE_SIZE_GROUPS` | dict | Ranges pt + OCR DPI + bias |

## Output structure

```
output/   hoặc   output/93.000.036.001.015/012345678901_NguyenVanA/
  02.Ly lich dang vien.pdf
  61.Ban tu kiem diem hang nam va khi chuyen sinh hoat dang.1.pdf
  _review/khac/KHAC_group_001.pdf
  _review/tentative/...
  _review/orphans/ORPHAN_page_0042.pdf
manifest.json
```

**Đặt tên Phụ lục 2:** `{STT}.{Ten}[.N].pdf` — STT pad 2 (01–99) hoặc 3 (100–104);
`.N` chỉ khi ≥2 bản cùng loại (năm cũ → mới).

`manifest.json` gồm success / review / tentative / orphans, `reattach_decisions`,
`validation.completeness_pct` (nếu tìm thấy Mục Lục).

## CLI options

| Flag | Description |
|------|-------------|
| `-i` / `--input` | PDF đầu vào (bắt buộc) |
| `-o` / `--output` | Thư mục xuất |
| `--dpi` | DPI render OCR |
| `--adaptive-dpi` | DPI theo size group |
| `--threshold` | Soft boundary score |
| `--pages N` | Chỉ N trang đầu |
| `--debug` | Log chi tiết |
| `--no-preprocess` | Bỏ deskew/CLAHE |
| `--m1..m5 --cccd --ho-ten` | Cây thư mục Phụ lục 2 |
| `--enable-continuation-llm` | Rule 3 LLM (Ollama) |

## Smoke tests

```bash
python -m pipeline.tests_sprint_improvements
```

## Project layout

```
pdf_splitter/
├── main.py
├── config.py
├── requirements.txt
├── pipeline/
│   ├── ingestor.py
│   ├── preprocessor.py
│   ├── ocr_engine.py
│   ├── signal_extractor.py
│   ├── end_of_doc_detector.py
│   ├── party_doc_matcher.py
│   ├── party_catalog.py
│   ├── boundary_detector.py
│   ├── continuation_validator.py
│   ├── orphan_reattacher.py
│   ├── manifest_extractor.py
│   ├── year_aware_sequencer.py
│   ├── pdf_exporter.py
│   ├── party_filename_resolver.py
│   ├── party_path_builder.py
│   └── tests_sprint_improvements.py
├── output/
└── logs/
```

## Troubleshooting

1. **OCR kém** — tăng `--dpi` / bật `--adaptive-dpi`, bỏ `--no-preprocess`.
2. **Orphan nhiều** — Pass-2 đã gắn sandwich / multi-page / same size; xem `reattach_decisions` trong manifest.
3. **Tách sai ranh giới** — chỉnh `HIGH_BOUNDARY_THRESHOLD`; xem `logs/low_confidence_pages.json`.
4. **Chạy chậm** — `--no-preprocess`, giảm DPI, `--pages 50` khi thử.
