# AGENTS.md — ngữ cảnh bắt buộc cho Cursor / agent

Đọc file này **trước** khi hỏi lại “hệ thống này là gì” hoặc đề xuất rewrite lớn.

## Đây là gì

CLI Python **bóc tách PDF scan hồ sơ đảng viên** theo Hướng dẫn **1361-CV/BTCTW**:

- Catalog **104 loại** (Phụ lục 1) → `pipeline/party_catalog.py`
- Tên file + cây thư mục Phụ lục 2 → `party_filename_resolver.py`, `party_path_builder.py`
- Chống nuốt tài liệu: 3-state `NEW` / `CONFIRMED_CONTINUATION` / `ORPHAN` → `boundary_detector.py`
- Entry: `main.py` · cấu hình: `config.py`

## Pipeline (đã ổn)

```
Ingest → Preprocess(tùy) → OCR PaddleOCR vi → Signal
  → Boundary 3-state → Pass-2 reattach (chỉ attach_prev)
  → audit/scrub → YearAwareSequencer → IdentityExtractor
  → PDFExporter + manifest.json (+ manifest_ho_so.json)
```

Output buckets:

| Bucket | Chỗ |
|--------|-----|
| success | `{STT}.{Ten}[.N].pdf` trong member dir |
| tentative | `_review/tentative/` |
| khac | `_review/khac/` |
| orphan | `_review/orphans/` |

Tên: `{STT}.{Ten khong dau}[.N].pdf` — STT 1–99 pad 2, 100–104 pad 3; `.N` chỉ khi ≥2 bản cùng loại.

## Đã chốt — ĐỪNG đụng trừ khi user bảo

- Logic tách trang + đặt tên trong `pipeline/` + `main.py` **đã ổn cho giai đoạn hiện tại**
- Không rewrite boundary / catalog / matcher “cho đẹp”
- Không hardcode theo 1 hồ sơ 195 trang (BDHN / Phạm Hữu Luật)
- Không chồng thêm pass (2e, 2f…) trong `main.py`
- TrOCR / Ollama **tắt mặc định** — không bật trừ khi được yêu cầu
- `_OCR_NAME_FIXUPS` (phm→Pham, lut→Luat) là vá hồ sơ mẫu — **đừng** nhân rộng theo tên người

## Hiện trạng (đến handoff này)

- **CLI + MinIO worker + HTTP API mỏng** (`api.py`, cổng 8090). Không GUI. Luồng: upload MinIO → `POST /api/jobs` → poll `GET /api/jobs/{id}`.
- Minh chứng full 195 trang: log `docs/run_full_demo.log` — ~57 file catalog, orphan ~1.5%, DPI 150 + no-preprocess. Zip output demo lưu ngoài repo (xem `docs/samples/README.md`).
- Tài liệu tốc độ: `docs/01-toc-do-va-tai-nguyen-may-chu.md` (+ bản `.doc` nếu có). OCR chiếm **>90%** thời gian. CPU vài giờ / 195 trang; GPU 8GB khoảng 5–15 phút.
- **Giao diện + REST API: đội dev khác.** Không prototype HTML / không `POST /api/jobs` trừ khi user bảo.

## Việc tiếp theo (khi user bảo làm)

Cắm **MinIO** vào đúng luồng CLI qua `.env`:

- Biến: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (+ prefix nếu cần)
- Kéo PDF gốc → chạy `main.py` như cũ → ghi `output/` (có thể đẩy cây output lên MinIO)
- **Không** viết backend API, **không** viết UI trong task MinIO

## Chạy nhanh

```bash
python main.py -i hoso.pdf -o ./output --dpi 150 --no-preprocess --cpu
python -m pipeline.tests_sprint_improvements
```

## Cách làm việc với agent

- Trả lời / sửa **đúng việc user hỏi**. Đừng lan man cải thiện 104 loại hay demo env nếu không được yêu cầu.
- Secret (`.env`, key MinIO) không commit.
- User nói tiếng Việt → trả lời tiếng Việt.
- Prompt dán chat mới: `docs/CURSOR_HANDOFF.md`
