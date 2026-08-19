#!/bin/bash
set -e

echo "========================================================="
echo "   ĐÓNG GÓI OFFLINE HỆ THỐNG AI BÓC TÁCH PDF"
echo "========================================================="

# a. Tạo thư mục tạm
echo "[1/6] Tạo thư mục tạm release_build..."
rm -rf release_build
mkdir -p release_build/phan_cua_ban_ai

# b. Lưu Docker Image
echo "[2/6] Đang nén Docker image pdf-splitter:latest..."
echo "      (Việc này sẽ mất vài phút tùy tốc độ ổ cứng)"
mkdir -p release_build/phan_cua_ban_ai/images
docker save -o release_build/phan_cua_ban_ai/images/pdf-splitter-latest.tar pdf-splitter:latest

# c. Copy file cấu hình, cài đặt và MÃ NGUỒN (CODE)
echo "[3/6] Đang copy mã nguồn và các file cấu hình..."
cp docker-compose.yml release_build/phan_cua_ban_ai/
cp .env.example release_build/phan_cua_ban_ai/
cp *.py release_build/phan_cua_ban_ai/ 2>/dev/null || true
cp requirements.txt release_build/phan_cua_ban_ai/ 2>/dev/null || true
cp Dockerfile release_build/phan_cua_ban_ai/ 2>/dev/null || true

if [ -d "pipeline" ]; then
    cp -r pipeline release_build/phan_cua_ban_ai/
fi

if [ -f install_ai.sh ]; then
    cp install_ai.sh release_build/phan_cua_ban_ai/
else
    echo "⚠️  Cảnh báo: Không tìm thấy file install_ai.sh"
fi

# d. Copy thư mục models (nếu có chứa GGUF)
if [ -d "models" ] && [ "$(ls -A models)" ]; then
    echo "[4/6] Đang copy thư mục models/ (chứa AI weights offline)..."
    cp -r models release_build/phan_cua_ban_ai/
else
    echo "[4/6] Thư mục models trống hoặc không tồn tại, bỏ qua."
fi

# e. Nén thành 1 file duy nhất
echo "[5/6] Đang nén toàn bộ thành file Deploy_AI_v1.tar..."
tar -cvf Deploy_AI_v1.tar -C release_build .

# f. Xóa dọn dẹp thư mục tạm
echo "[6/6] Dọn dẹp thư mục tạm release_build..."
rm -rf release_build

echo "========================================================="
echo "✅ HOÀN TẤT! Đã tạo thành công file: Deploy_AI_v1.tar"
echo "   Bạn hãy tải file này mang đi cài đặt cho khách hàng."
echo "========================================================="
