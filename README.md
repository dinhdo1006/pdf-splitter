# Vietnamese PDF Auto-Splitter (Hồ sơ đảng viên — 3-state)

Tách PDF scan hồ sơ đảng viên hỗn hợp theo catalog Phụ lục 1361 (104 loại), chống nuốt tài liệu (NEW / CONFIRMED_CONTINUATION / ORPHAN), đặt tên theo Phụ lục 2.

Repo: https://github.com/dinhdo1006/pdf-splitter

## Installation (Linux / server GPU)

```bash
cd ~/Downloads   # hoặc thư mục bạn chọn
git clone https://github.com/dinhdo1006/pdf-splitter.git
cd pdf-splitter

sudo apt install -y python3-venv python3-full   # nếu chưa có
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

Cập nhật code sau này:

```bash
cd ~/path/to/pdf-splitter
source .venv/bin/activate
git pull origin main
```

> **GPU:** đặt `OCR_USE_GPU = True` trong `config.py`. Có thể cần cài `paddlepaddle-gpu` thay cho `paddlepaddle` CPU.

## First run

```bash
# Test nhanh
python main.py -i path/to/hoso.pdf -o ./output --pages 10 --dpi 200

# Full
python main.py -i path/to/hoso.pdf -o ./output --dpi 200
```

## How it works

Pipeline: Ingest → Preprocess → OCR → catalog matcher + **3-state boundary** (NEW / CONFIRMED_CONTINUATION / ORPHAN) → YearAwareSequencer → export `STT.Ten[.N].pdf` + `_review/orphans/`.

## Configuration

Các hằng số nằm trong `config.py`:


| Setting | Default | Meaning |
|---------|---------|---------|
| `PDF_RENDER_DPI` | `200` | Độ phân giải render cho OCR (150–300) |
| `MAX_SKEW_ANGLE` | `10.0` | Góc nghiêng tối đa tìm khi deskew |
| `SKEW_THRESHOLD` | `1.5` | Chỉ xoay nếu góc phát hiện lớn hơn giá trị này |
| `OCR_LANG` | `"vi"` | Ngôn ngữ OCR |
| `OCR_USE_GPU` | `False` | Bật GPU cho PaddleOCR |
| `OCR_MIN_CONFIDENCE` | `0.40` | Bỏ block OCR dưới ngưỡng này |
| `HEADER_ZONE_BOTTOM` | `0.33` | Vùng header = 1/3 trên của trang |
| `KEYWORD_FUZZY_THRESHOLD` | `85` | Ngưỡng rapidfuzz cho từ khoá OCR lỗi |
| `BOUNDARY_THRESHOLD` | `0.45` | Điểm ≥ ngưỡng → coi là văn bản mới |
| `BLANK_PAGE_DENSITY` | `0.05` | Mật độ chữ dưới mức này → trang trắng |
| `HEADER_SIMILARITY_THRESHOLD` | `0.85` | Header giống nhau → cùng văn bản |
| `MAX_SLUG_LENGTH` | `60` | Độ dài tối đa tên file (slug) |

## Output structure

```
output/
├── HOP_DONG_LAO_DONG_01.pdf
├── HOA_DON_GTGT_01.pdf
├── BAO_CAO_TAI_CHINH_01.pdf
└── manifest.json
```

**Quy ước đặt tên:** tiêu đề trang đầu → bỏ dấu (unidecode) → UPPERCASE → ký tự không hợp lệ thành `_` → thêm `_01`, `_02`… nếu trùng. Nếu file đã tồn tại, thêm `_v2`, `_v3` (không ghi đè).

**`manifest.json`** chứa:

```json
{
  "total_documents": 3,
  "source_pdf": "...",
  "exported_at": "2026-07-31T07:00:00+00:00",
  "documents": [
    {
      "group_id": 1,
      "filename": "HOP_DONG_LAO_DONG_01.pdf",
      "output_path": "...",
      "page_count": 3,
      "page_range": [1, 3],
      "raw_title": "HỢP ĐỒNG LAO ĐỘNG"
    }
  ]
}
```

Trang nghi ngờ (điểm biên gần ngưỡng) được ghi vào `logs/low_confidence_pages.json` để review thủ công.

## CLI options

| Flag | Description |
|------|-------------|
| `--input` / `-i` | Đường dẫn PDF đầu vào (**bắt buộc**) |
| `--output` / `-o` | Thư mục xuất (mặc định `./output`) |
| `--dpi` | DPI render OCR |
| `--threshold` | Ngưỡng điểm biên |
| `--debug` | Log chi tiết từng trang |
| `--no-preprocess` | Bỏ deskew/CLAHE (nhanh hơn) |
| `--pages N` | Chỉ xử lý N trang đầu (test) |

## Troubleshooting

1. **OCR kém / sai chữ** — tăng `--dpi` (ví dụ 250–300), kiểm tra chất lượng scan, hoặc bỏ `--no-preprocess` để bật deskew.
2. **Tách sai ranh giới** — chỉnh `BOUNDARY_THRESHOLD` trong `config.py` hoặc `--threshold` (giảm → nhiều văn bản hơn; tăng → gộp nhiều trang hơn). Xem `logs/low_confidence_pages.json`.
3. **Chạy chậm** — dùng `--no-preprocess`, giảm DPI xuống 150, hoặc giới hạn `--pages 50` khi thử nghiệm. GPU: cài `paddlepaddle-gpu` + `OCR_USE_GPU = True`.

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
│   ├── boundary_detector.py
│   ├── title_normalizer.py
│   └── pdf_exporter.py
├── models/
├── output/
└── logs/
```

Mỗi module có block `# Quick test` (`if __name__ == "__main__"`) để chạy độc lập:

```bash
python -m pipeline.title_normalizer
python -m pipeline.boundary_detector
python -m pipeline.ingestor path/to.pdf
```
