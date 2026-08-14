# Tích hợp Cách A — cho đội backend

Luồng chuẩn: **upload MinIO → gọi worker → poll status → lấy output**.

## 1. Upload đầu vào

| Mục | Giá trị |
|-----|---------|
| Endpoint API | `10.10.4.21:9000` (UI console `:9001`) |
| Bucket | `hsdv-pdf-splitter` |
| Object key | `inbox/{job_id}.pdf` |
| `job_id` | Chuỗi do backend chọn (UUID, mã hồ sơ, …) |

Ví dụ: `s3://hsdv-pdf-splitter/inbox/hs_20260812_001.pdf`

## 2. Kích hoạt bóc tách (worker)

Sau upload thành công, gọi **một** trong các cách:

```bash
cd /path/to/pdf_splitter
source .venv/bin/activate
python minio_trigger.py hs_20260812_001
```

Hoặc subprocess từ backend:

```bash
python minio_trigger.py {job_id}
```

Mặc định: `--dpi 150 --no-preprocess`, OCR GPU **auto** (`config.OCR_USE_GPU`).

Test vài trang:

```bash
python minio_trigger.py hs_20260812_001 --pages 10
```

Ép GPU:

```bash
python minio_trigger.py hs_20260812_001 --gpu
```

Tương đương đầy đủ:

```bash
python minio_run.py --job-id {job_id} --dpi 150 --no-preprocess
```

## 3. Poll trạng thái

| Mục | Giá trị |
|-----|---------|
| Object key | `jobs/{job_id}/status.json` |

Trường quan trọng:

| Field | Ý nghĩa |
|-------|---------|
| `status` | `running` → chờ; `completed` / `failed` |
| `output_prefix` | `output/{job_id}/` |
| `manifest_key` | `output/{job_id}/manifest.json` |
| `error` | Lỗi nếu `failed` |
| `stats` | `success_count`, `orphan_count`, `completeness_pct`, … |

Ví dụ `status.json` khi xong:

```json
{
  "job_id": "hs_20260812_001",
  "status": "completed",
  "input_key": "inbox/hs_20260812_001.pdf",
  "output_prefix": "output/hs_20260812_001/",
  "manifest_key": "output/hs_20260812_001/manifest.json",
  "archived_input_key": "archive/inbox/hs_20260812_001.pdf",
  "bucket": "hsdv-pdf-splitter",
  "stats": {
    "success_count": 57,
    "orphan_count": 3,
    "completeness_pct": 100
  }
}
```

## 4. Lấy đầu ra

List/download mọi object dưới `output/{job_id}/`:

- File catalog: `02.Ly lich dang vien.pdf`, …
- Review: `_review/orphans/`, `_review/tentative/`, `_review/khac/`
- Metadata: `manifest.json`

Backend có thể tạo presigned GET URL (MinIO SDK) cho từng file.

## 5. Luồng API gợi ý (backend dev)

```
POST /api/hoso/split
  → tạo job_id
  → upload PDF → inbox/{job_id}.pdf
  → subprocess: python minio_trigger.py {job_id}
  → trả job_id cho frontend

GET /api/hoso/split/{job_id}
  → đọc jobs/{job_id}/status.json
  → nếu completed: list output/{job_id}/
```

## 6. Cấu hình MinIO (server)

File `minio_s3_config.json` (không commit) + `python scripts/write_env_from_s3.py`.

Chi tiết bucket/prefix: `MINIO_FLOW.md`.

## 7. Không làm trong repo này

- REST API / UI người dùng (đội dev khác)
- Tự poll inbox 24/7 (Cách B) — không dùng trong Cách A
