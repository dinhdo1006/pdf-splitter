# Yêu cầu 1 — Tốc độ xử lý 1 bộ hồ sơ, hạ tài nguyên máy chủ, khuyến nghị cấu hình

Tài liệu phục vụ nghiệm thu / báo cáo kỹ thuật.  
**Phạm vi:** tốc độ và tài nguyên. Không mô tả lại logic tách trang / đặt tên (đã ổn định).  
**Mốc đo:** bộ hồ sơ mẫu *Hồ sơ Đảng viên hiện trạng BDHN* — **195 trang** scan.  
**Minh chứng đầu ra:** `D:\output_full_demmo.zip` (xử lý full 195 trang, đóng gói ngày 11/08/2026).

---

## 1. Bài toán tốc độ

Một “bộ hồ sơ” = **một file PDF scan hỗn hợp** (thường 80–250 trang). Hệ thống:

1. Render từng trang thành ảnh  
2. OCR tiếng Việt (PaddleOCR)  
3. Phân loại / cắt ranh giới / đặt tên theo catalog 104 loại  
4. Xuất nhiều PDF nhỏ + `manifest.json`

**> 90% thời gian nằm ở bước OCR.**  
Cắt ranh giới, đặt tên, ghi file PDF gốc (copy trang, không render lại) chỉ mất vài giây đến dưới 1 phút / bộ.

Do đó: muốn nhanh thì tăng sức OCR (GPU). Muốn hạ tài nguyên thì chấp nhận OCR chậm hơn, không đụng logic.

Pipeline **xử lý tuần tự từng trang** (một lúc một ảnh) — RAM không phình theo số trang. Phù hợp máy chủ nhỏ; đổi lại CPU/GPU phải chạy hết 195 lần OCR.

---

## 2. Số liệu đo thực tế (máy phát triển Windows, CPU, không GPU)

Cấu hình chạy: PaddleOCR `device=cpu`, `mkldnn=off`, **không** TrOCR, **không** LLM/Ollama.

| Điều kiện | Thời gian / trang | Ước lượng 1 bộ 195 trang |
|---|---|---|
| DPI 120, `--no-preprocess` | ~45–90 giây | **~2,5–5 giờ** |
| DPI 150, `--no-preprocess` | ~1–2 phút | **~3–6,5 giờ** |
| DPI 200 + deskew/CLAHE | ~2–5 phút | **~6–15 giờ** (không khuyến nghị cho máy CPU yếu) |

Ghi chú:

- Trang đầu chậm hơn (nóng model). Trang sau ổn định hơn.  
- Cache OCR (`--use-ocr-cache`): lần 2 **bỏ qua OCR** → chỉ còn vài chục giây đến 1–2 phút cho cả bộ (phân loại + xuất file).  
- Chạy full 195 trang trên máy Linux (`/home/sonth/…`, DPI 150, no-preprocess) đã **hoàn tất** — kết quả đóng trong `output_full_demmo.zip`. Thời gian tường máy Linux không nằm trong zip; bảng trên là đo trên máy Windows CPU.

**Kết luận đo:** máy chủ **chỉ CPU** xử lý được, nhưng **không đảm bảo tốc độ** cho demo / nghiệm thu trực tiếp (người chờ). Muốn “chạy xong trong buổi họp” cần **GPU**.

---

## 3. Kết quả 1 bộ đã xử lý (minh chứng zip)

File: `D:\output_full_demmo.zip` (~57 MB)

| Chỉ số | Giá trị |
|---|---|
| Trang xử lý | 195 / 195 (coverage 100%, không mất trang) |
| File PDF xuất | 61 file |
| Tài liệu catalog (success) | 57 |
| Review / KHAC | 1 |
| Orphan | 3 trang (**1,5%**) |
| Trang trắng (bỏ) | 18 |
| Cây thư mục Phụ lục 2 | `00.000.000.000.000/2772699_PhamHuuLuat/` |

Zip gồm cả `_ocr_cache/dpi150_noprep/` (195 file JSON) — phục vụ chạy lại nhanh. Khi **nộp khách** có thể gói lại **không kèm cache** cho nhẹ hơn.

---

## 4. Hạ tài nguyên máy chủ (chạy được máy yếu)

Mục tiêu: vẫn ra đúng file, máy ít CPU/RAM/không GPU.

### 4.1. Việc đã tắt sẵn (không tốn tài nguyên thêm)

| Thành phần | Mặc định | Ảnh hưởng |
|---|---|---|
| TrOCR (viết tay) | `ENABLE_HANDWRITING_OCR = False` | Không chiếm RAM GPU/CPU thêm |
| LLM Ollama | `ENABLE_CONTINUATION_LLM = False` | Không chiếm VRAM |
| MKLDNN | tắt | Tránh xung đột / RAM OneDNN |

**Không bật** các cờ này trên máy chủ nhỏ.

### 4.2. Cách chạy nhẹ (khuyến nghị khi hạ tài nguyên)

```bash
python main.py -i hoso.pdf -o ./output --cpu --dpi 150 --no-preprocess
```

| Cờ / thiết lập | Tác dụng hạ tài nguyên |
|---|---|
| `--cpu` | Không dùng GPU; máy không card vẫn chạy |
| `--dpi 150` | Ảnh nhỏ hơn DPI 200 → OCR nhẹ hơn, RAM thấp hơn |
| `--no-preprocess` | Bỏ deskew/CLAHE (OpenCV) — bớt CPU |
| Không `--enable-continuation-llm` | Không gọi Ollama |
| 1 tiến trình / 1 hồ sơ | Tránh 2 PaddleOCR cùng lúc (RAM x2) |

RAM thực tế khi chạy 1 hồ sơ (CPU, DPI 150): **khoảng 3–6 GB** (Python + PaddleOCR + 1 trang ảnh). Không cần 32 GB.

CPU: PaddleOCR dùng nhiều nhân. Máy 4 nhân vẫn chạy; 8 nhân CPU-only sẽ nhanh hơn rõ (vẫn chậm hơn GPU rất nhiều).

### 4.3. Chạy lại không tốn OCR

```bash
python main.py -i hoso.pdf -o ./output --use-ocr-cache --dpi 150 --no-preprocess
```

Lần 1: chậm (OCR). Lần 2: nhanh (đọc JSON cache). Phù hợp chỉnh logic / demo lại cùng bộ hồ sơ.

### 4.4. Trade-off khi hạ tài nguyên

| Hạ | Được | Mất |
|---|---|---|
| DPI 120–150 | Nhanh hơn, RAM thấp | OCR trang chữ nhỏ / scan mờ kém hơn |
| `--no-preprocess` | Bớt CPU | Trang lệch góc đọc kém hơn |
| `--cpu` | Không cần card | Chậm 10–30 lần so với GPU |

**Mức hạ an toàn cho nghiệm thu độ đúng:** DPI **150**, `--no-preprocess`, `--cpu` — đúng profile đã ra `output_full_demmo.zip`.  
Không nên xuống dưới DPI 120 nếu còn cần đọc header để đặt tên.

---

## 5. Khuyến nghị tài nguyên máy chủ để **đảm bảo tốc độ**

“Đảm bảo tốc độ” hiểu là: **1 bộ ~150–200 trang xong trong thời gian chấp nhận được khi demo / vận hành**.

### 5.1. Ba mức cấu hình

#### A. Tối thiểu — chạy được, không cam kết tốc độ (máy dev / thử)

| Thành phần | Mức |
|---|---|
| CPU | 4 nhân |
| RAM | 8 GB |
| GPU | Không |
| Ổ đĩa | SSD 40 GB trống (model PaddleOCR + output) |
| OS | Linux x86_64 (Ubuntu 22.04) hoặc Windows 10/11 |

**Tốc độ kỳ vọng:** 1 bộ 195 trang ≈ **3–6 giờ**.  
Dùng khi: xử lý nền, chạy đêm, không ai ngồi chờ.

#### B. Khuyến nghị — đảm bảo tốc độ demo / vận hành 1 luồng *(chọn mức này)*

| Thành phần | Mức |
|---|---|
| CPU | 8 nhân |
| RAM | 16 GB |
| GPU | NVIDIA **8 GB VRAM** (T4, L4, RTX 3060/4060 trở lên) |
| Driver | CUDA tương thích PaddlePaddle GPU |
| Ổ đĩa | SSD NVMe, ≥ 80 GB trống |
| OS | **Ubuntu 22.04 LTS** (Paddle GPU ổn định hơn Windows) |

**Tốc độ kỳ vọng (GPU, DPI 150–200):** khoảng **0,5–3 giây / trang**  
→ 1 bộ 195 trang ≈ **5–15 phút** (cộng khởi tạo model ~20–40 giây).

Lệnh:

```bash
python main.py -i hoso.pdf -o ./output --gpu --dpi 150 --no-preprocess
# hoặc chất lượng OCR cao hơn một chút:
python main.py -i hoso.pdf -o ./output --gpu --dpi 200 --adaptive-dpi
```

`OCR_USE_GPU = "auto"`: có CUDA + VRAM trống ≥ 1,5 GB thì dùng GPU; thiếu VRAM thì tự về CPU (chậm). **Không chạy Ollama trên cùng GPU** khi OCR — Ollama chiếm VRAM sẽ đẩy hệ thống xuống CPU (`OCR_GPU_MIN_FREE_MB = 1500`).

#### C. Vận hành nhiều hồ sơ / ngày (tùy chọn)

| Thành phần | Mức |
|---|---|
| CPU | 16 nhân |
| RAM | 32 GB |
| GPU | NVIDIA 16 GB (T4 16GB / RTX 4080 / A4000) |
| Hàng đợi | 1 job OCR / GPU (không song song 2 Paddle trên 1 card 8 GB) |

Công suất thô: ~8–20 bộ hồ sơ 200 trang / giờ / 1 GPU (nếu ~10 phút/bộ).  
Muốn song song 2 hồ sơ: cần 2 GPU hoặc VRAM ≥ 16 GB và kiểm tra OOM.

### 5.2. Bảng so sánh nhanh

| Mức | GPU | 1 bộ 195 trang | Phù hợp |
|---|---|---|---|
| A. Tối thiểu | Không | 3–6 giờ | Dev, chạy nền |
| **B. Khuyến nghị** | **8 GB** | **5–15 phút** | **Demo, nghiệm thu tốc độ** |
| C. Vận hành | 16 GB | 5–10 phút / bộ, nhiều job hơn | Sản xuất |

---

## 6. Cách trình bày “tốc độ 1 bộ hồ sơ” khi nghiệm thu

Nên công bố **hai con số**, tránh hiểu nhầm:

1. **Lần đầu (có OCR)**  
   - CPU (mức A): vài giờ / bộ 195 trang  
   - GPU (mức B): khoảng **5–15 phút** / bộ 195 trang  

2. **Lần sau (có OCR cache)**  
   - Cùng máy, cùng DPI: **dưới 2 phút** / bộ (chỉ tách + xuất)

Công thức xấp xỉ:

```
Thời gian ≈ (số trang × thời gian OCR/trang) + 1 phút xuất file
```

Ràng buộc tài nguyên thấp **không** làm sai logic; chỉ làm **chậm bước OCR**.  
Ràng buộc tốc độ demo → **bắt buộc GPU mức B**.

---

## 7. Checklist vận hành máy chủ nhỏ (không chết máy)

- [ ] Chỉ **1** `python main.py` tại một thời điểm  
- [ ] Tắt Ollama / TrOCR trên máy OCR  
- [ ] `--cpu` nếu không có GPU hoặc VRAM < 4 GB  
- [ ] DPI 150 + `--no-preprocess` khi RAM ≤ 8 GB  
- [ ] SSD (HDD làm OCR rất chậm vì swap)  
- [ ] Sau mỗi ~50–100 trang hệ thống `gc.collect()` — không cần chỉnh  
- [ ] Không mở nhiều PDF 200 trang trong GUI cùng lúc với OCR  

---

## 8. Việc chưa làm (cố ý, làm sau)

- Môi trường demo đóng gói (mục 3) — **để sau**, theo yêu cầu.  
- Script tự zip output khi `main.py` xong — zip hiện tại đã tạo tay từ `output_full_demmo`.  
- Đo lại **đúng phút** trên máy Linux GPU (cần 1 lần chạy có log `Done.` + timestamp) để ghi SLA chính thức thay cho khoảng 5–15 phút.

Khi có log GPU đầy đủ, chỉ cần thay số ở mục 5.1.B; phần hạ tài nguyên (mục 4) giữ nguyên.
