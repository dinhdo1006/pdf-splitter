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

## 2. Kích hoạt bóc tách — HTTP API (web gọi cái này)

Web **không** gọi file Python trên disk. Gọi HTTP trên máy chạy worker.

Mặc định: `http://<IP-server-GPU>:8090`

| Method | URL | Việc |
|--------|-----|------|
| `GET` | `/health` | API sống |
| `POST` | `/api/jobs` | Bắt đầu bóc (PDF đã nằm `inbox/{job_id}.pdf`) |
| `GET` | `/api/jobs/{job_id}` | Poll trạng thái |

**POST** — body JSON:

```json
{ "job_id": "bdhn_full" }
```

Tuỳ chọn: `"pages": 5`, `"dpi": 150`, `"gpu": true`, `"debug": true`

Trả **202**:

```json
{
  "job_id": "bdhn_full",
  "status": "accepted",
  "poll_url": "/api/jobs/bdhn_full",
  "inbox_key": "inbox/bdhn_full.pdf",
  "output_prefix": "output/bdhn_full/"
}
```

**GET** trả nội dung `status.json` (`running` / `completed` / `failed`).

Ví dụ curl:

```bash
curl -X POST http://IP_SERVER:8090/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_id":"bdhn_full"}'

curl http://IP_SERVER:8090/api/jobs/bdhn_full
```

Frontend/backend **không** exec:

`.../.venv/bin/python .../minio_trigger.py`

CLI chỉ dùng khi test SSH trên server.

### CLI (test tay, không dùng cho web)

```bash
cd /path/to/pdf_splitter
source .venv/bin/activate
python minio_trigger.py hs_20260812_001
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

## 5. Luồng web / backend dev

```
User upload trên web
  → backend upload PDF → MinIO inbox/{job_id}.pdf   (10.10.4.21:9000)
  → backend POST http://<IP-GPU>:8090/api/jobs  { "job_id": "..." }
  → frontend poll GET http://<IP-GPU>:8090/api/jobs/{job_id}
  → completed → list/download MinIO output/{job_id}/
```

## 6. Cấu hình MinIO (server)

File `minio_s3_config.json` (không commit) + `python scripts/write_env_from_s3.py`.

Chi tiết bucket/prefix: `MINIO_FLOW.md`.

## 7. Chạy API trên server GPU

```bash
cd ~/Downloads/Hệ\ thống\ bóc\ tách\ pdf/pdf_splitter
source .venv/bin/activate
pip install fastapi uvicorn
python api.py
```

Lắng nghe `0.0.0.0:8090`. Mở firewall port **8090** nếu web gọi từ máy khác.

UI người dùng (form upload) vẫn do đội dev — API này chỉ **start job + poll status**.
