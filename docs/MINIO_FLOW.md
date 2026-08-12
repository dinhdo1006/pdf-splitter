# Luồng MinIO — hợp đồng cho đội dev ghép hệ thống

Bucket mặc định: **`hsdv-pdf-splitter`** (tự tạo khi chạy `python minio_run.py --setup`).

## Sơ đồ

```
Dev / API upload PDF
        ↓
  inbox/{job_id}.pdf
        ↓
  minio_run.py (worker)
    1. status → jobs/{job_id}/status.json (running)
    2. tải PDF → work_minio/{job_id}/input.pdf
    3. python main.py -i ... -o work_minio/{job_id}/output
    4. upload cây output → output/{job_id}/
    5. status → completed + stats từ manifest.json
    6. (tuỳ chọn) inbox → archive/inbox/{job_id}.pdf
```

## Cấu trúc bucket

| Prefix | Vai trò | Ai ghi |
|--------|---------|--------|
| `inbox/` | PDF gốc chờ xử lý | **Dev / API upload** |
| `output/{job_id}/` | File tách + `manifest.json` | Worker (`minio_run.py`) |
| `jobs/{job_id}/status.json` | Trạng thái job (poll) | Worker |
| `archive/inbox/` | PDF đã xử lý (backup inbox) | Worker |

### Ví dụ output sau khi chạy job `bdhn_001`

```
hsdv-pdf-splitter/
  inbox/                          ← trống sau archive
  archive/inbox/bdhn_001.pdf
  jobs/bdhn_001/status.json
  output/bdhn_001/
    manifest.json
    02.Ly lich dang vien.pdf
    61.Ban tu kiem diem....1.pdf
    _review/orphans/...
    _review/tentative/...
    93.000.036.001.015/012345678901_NguyenVanA/   ← nếu OCR đủ identity
```

**Không upload:** `_ocr_cache/`, `__pycache__/`.

## `status.json` (cho API poll)

```json
{
  "job_id": "bdhn_001",
  "status": "running | completed | failed",
  "input_key": "inbox/bdhn_001.pdf",
  "output_prefix": "output/bdhn_001/",
  "manifest_key": "output/bdhn_001/manifest.json",
  "archived_input_key": "archive/inbox/bdhn_001.pdf",
  "bucket": "hsdv-pdf-splitter",
  "started_at": "2026-08-12T08:00:00+00:00",
  "finished_at": "2026-08-12T08:15:00+00:00",
  "error": null,
  "stats": {
    "success_count": 57,
    "tentative_count": 2,
    "orphan_count": 3,
    "completeness_pct": 100,
    "orphan_rate_pct": 1.5
  },
  "pipeline_args": ["--dpi", "150", "--no-preprocess", "--cpu"],
  "updated_at": "2026-08-12T08:15:00+00:00"
}
```

## Cách dev tích hợp

### 1. Upload đầu vào

- Upload PDF lên `s3://hsdv-pdf-splitter/inbox/{job_id}.pdf`
- `job_id` = tên file không đuôi (vd. `bdhn_001`)

### 2. Kích hoạt worker

- **Cách A:** cron / scheduler gọi `python minio_run.py --poll`
- **Cách B:** API backend gọi subprocess `python minio_run.py --job-id {job_id}`
- **Cách C:** `python minio_run.py --key inbox/{job_id}.pdf`

### 3. Đọc đầu ra

- Poll `jobs/{job_id}/status.json` đến khi `status == completed`
- List/download objects dưới `output/{job_id}/`
- `manifest.json` = metadata đầy đủ (success, orphan, reattach…)

### 4. Presigned URL (gợi ý backend)

Backend dùng cùng MinIO client tạo presigned GET cho file trong `output/{job_id}/` — không cần implement trong repo splitter.

## Cài đặt & cấu hình

Cách 1 — file JSON (khuyến nghị, cùng format S3 app):

```bash
cp minio_s3_config.example.json minio_s3_config.json
nano minio_s3_config.json
python scripts/write_env_from_s3.py
python minio_run.py --setup
```

Dùng bucket riêng `hsdv-pdf-splitter` (tự tạo). Nếu dùng bucket `data-lake`, sửa `bucket` trong JSON và set prefix:

```bash
export MINIO_PREFIX_INBOX=hsdv-pdf-splitter/inbox
export MINIO_PREFIX_OUTPUT=hsdv-pdf-splitter/output
export MINIO_PREFIX_ARCHIVE=hsdv-pdf-splitter/archive/inbox
export MINIO_PREFIX_STATUS=hsdv-pdf-splitter/jobs
```

Cách 2 — `.env` thủ công: copy `.env.example`, điền key.


## Lệnh thường dùng

```bash
# Tạo bucket
python minio_run.py --setup

# Một job
python minio_run.py --job-id bdhn_001 --dpi 150 --no-preprocess --cpu

# Quét inbox
python minio_run.py --poll --dpi 150 --no-preprocess --cpu

# Giữ file tạm để debug
python minio_run.py --job-id test --keep-work --pages 5
```

## Ghi chú

- Port API MinIO: **9000** (UI console thường **9001**).
- Worker **không** là REST API — đội dev bọc `minio_run.py` hoặc import `minio_store` nếu cần.
- Pipeline logic vẫn trong `main.py` — không đụng boundary/catalog.
