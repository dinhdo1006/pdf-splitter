# CHỈ CẦN DÁN 1 PROMPT NÀY

Sang chat Cursor mới (code `pdf_splitter` đã mở):

1. Copy **toàn bộ** khối từ dòng `Bạn đang trong repo` đến hết `Chờ user giao việc.`
2. Dán vào ô chat → Enter.
3. Xong. Không cần `@` file khác.

---

Bạn đang trong repo pdf_splitter — hệ thống bóc tách PDF hồ sơ đảng viên (Hướng dẫn 1361-CV/BTCTW).

ĐỌC NHANH nếu cần chi tiết code: AGENTS.md, README.md, main.py, config.py, pipeline/boundary_detector.py, party_catalog.py. Đừng hỏi lại “hệ thống này là gì”.

BÀI TOÁN (đã chốt):
- Input: 1 PDF scan hỗn hợp (80–250 trang) hồ sơ đảng viên.
- Output: nhiều PDF nhỏ tên Phụ lục 2 `{STT}.{Ten khong dau}[.N].pdf` trong cây `M1.M2.M3.M4.M5/{CCCD_hoac_TDV}_{HoTen}/`.
- Catalog 104 loại. Đúng key → tên đúng. Không chắc → orphan, không tự gộp vào tài liệu trước.
- 4 bucket: success | _review/tentative | _review/khac | _review/orphans.
- Pipeline: Ingest → OCR PaddleOCR vi → Signal → Boundary 3-state (NEW/CONFIRMED_CONTINUATION/ORPHAN) → Pass-2 attach_prev → export + manifest.

ĐÃ ỔN — ĐỪNG ĐỤNG trừ khi user bảo:
- Logic tách + đặt tên trong pipeline/ + main.py.
- Không rewrite boundary/catalog/matcher; không hardcode hồ sơ BDHN / Phạm Hữu Luật; không thêm pass trong main.py; TrOCR/Ollama tắt mặc định.

HIỆN TRẠNG:
- CLI + MinIO worker (`minio_run.py`, `minio_store.py`, `.env.example`). Hợp đồng: docs/MINIO_FLOW.md.
- Chưa FastAPI, chưa GUI. Giao diện + REST API = đội dev khác bọc worker hoặc poll status.json.

CÁCH LÀM: trả lời đúng việc được hỏi, tiếng Việt, không lan man, không commit .env/secret.

Bắt đầu: tóm tắt 5–8 gạch (bài toán, CLI, 4 bucket, việc cấm, việc tiếp theo MinIO+.env). Không hỏi lại toàn bộ hệ thống. Chờ user giao việc.
