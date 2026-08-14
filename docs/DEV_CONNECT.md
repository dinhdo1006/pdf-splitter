# Hướng dẫn đội Dev — cần kết nối những gì

Hệ thống bóc tách PDF **không** gọi từ trình duyệt vào file Python. Dev kết nối **2 dịch vụ**:

1. **MinIO** — lưu file PDF vào / lấy file ra  
2. **HTTP API worker** — bảo hệ thống bắt đầu bóc và hỏi đã xong chưa  

Không kết nối Kafka. Không SSH để chạy `minio_trigger.py` từ web.

---

## 1. MinIO (lưu trữ file)

| Mục | Giá trị |
|-----|---------|
| API (S3) | `http://10.10.4.21:9000` |
| UI console | `http://10.10.4.21:9001` |
| SSL | `false` |
| Bucket | `hsdv-pdf-splitter` |
| Access key / Secret | Xin admin MinIO (cùng key hệ thống, hoặc key riêng quyền bucket này) |

Prefix trong bucket:

| Path | Ai ghi | Ý nghĩa |
|------|--------|---------|
| `inbox/{job_id}.pdf` | **Dev upload** | PDF gốc chờ bóc |
| `output/{job_id}/` | Worker | File đã tách + `manifest.json` |
| `jobs/{job_id}/status.json` | Worker | Trạng thái (cũng đọc qua HTTP API) |
| `archive/inbox/{job_id}.pdf` | Worker | PDF gốc sau khi bóc xong |

`job_id` = chuỗi do **dev tạo** (UUID / mã hồ sơ). Phải trùng tên file, **không** có `.pdf`.  
Ví dụ upload `inbox/hs_001.pdf` → `job_id` = `hs_001`.

---

## 2. HTTP API bóc tách (máy GPU / worker)

Chạy trên **máy cài `pdf_splitter` + GPU** (không phải cổng MinIO).

| Mục | Giá trị |
|-----|---------|
| Base URL | `http://<IP-máy-worker>:8090` |
| Ví dụ nếu API chạy trên server `sonth` | `http://10.10.6.134:8090` |
| Health | `GET /health` → `{"ok": true}` |

Xác nhận IP thật: trên máy worker chạy `python api.py`, hỏi admin IP LAN của máy đó. **Không** dùng `:9000`.

### API

**Bắt đầu bóc** — PDF **đã** nằm `inbox/{job_id}.pdf`:

```http
POST /api/jobs
Content-Type: application/json

{ "job_id": "hs_001" }
```

HTTP **202**. OCR chạy nền, không đợi xong trong request này.

Tuỳ chọn: `"pages": 5`, `"dpi": 150`, `"gpu": true`

**Hỏi trạng thái:**

```http
GET /api/jobs/hs_001
```

| `status` | Việc frontend/backend làm |
|----------|---------------------------|
| `queued_or_starting` / `running` | Tiếp tục poll (vài giây/lần) |
| `completed` | Lấy file từ MinIO `output/hs_001/` |
| `failed` | Hiện `error` |

---

## 3. Thứ tự bắt buộc

```
1. Dev tạo job_id  (vd. hs_001)
2. Dev upload PDF  →  MinIO  inbox/hs_001.pdf     (cổng 9000)
3. Dev POST        →  http://<IP-worker>:8090/api/jobs
                      body { "job_id": "hs_001" }
4. Dev poll        →  GET http://<IP-worker>:8090/api/jobs/hs_001
5. Khi completed   →  download MinIO output/hs_001/
```

Bỏ bước 2 → POST trả **404**.  
Bỏ bước 3 → file nằm inbox, **không** tự bóc.

---

## 4. Không làm

- Không gọi `.../.venv/bin/python .../minio_trigger.py` từ web  
- Không upload PDF vào API worker (API không nhận file; file đi MinIO)  
- Không dùng cổng `9001` cho SDK (9001 chỉ UI)  
- Không hardcode `{job_id}` thành chữ `{job_id}`

---

## 5. Curl test

```bash
# 1) Health worker
curl http://10.10.6.134:8090/health

# 2) Sau khi đã upload inbox/hs_001.pdf lên MinIO
curl -X POST http://10.10.6.134:8090/api/jobs \
  -H "Content-Type: application/json" \
  -d "{\"job_id\":\"hs_001\"}"

# 3) Poll
curl http://10.10.6.134:8090/api/jobs/hs_001
```

(Thay `10.10.6.134` bằng IP máy đang chạy `python api.py` nếu khác.)
