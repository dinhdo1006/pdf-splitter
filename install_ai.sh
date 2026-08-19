#!/bin/bash

echo "========================================================"
echo " BẮT ĐẦU CÀI ĐẶT PHÂN HỆ AI (BÓC TÁCH PDF)"
echo "========================================================"

# 0. KIỂM TRA MÔI TRƯỜNG YÊU CẦU
echo "--> [0/5] Kiểm tra Docker và NVIDIA Container Toolkit..."
if ! command -v docker &> /dev/null; then
    echo "[LỖI] Chưa cài đặt Docker! Vui lòng cài Docker trước khi chạy script."
    exit 1
fi

if ! docker info | grep -i "Runtimes.*nvidia" &> /dev/null; then
    echo "⚠️  [CẢNH BÁO] Chưa tìm thấy NVIDIA Runtime (nvidia-container-toolkit) trong Docker."
    echo "    Nếu khởi động báo lỗi thiếu GPU, vui lòng cài đặt nvidia-container-toolkit!"
fi

# 1. TẠO THƯ MỤC VÀ PHÂN QUYỀN (PRE-RUN SETUP)
echo "--> [1/5] Khởi tạo các thư mục lưu trữ vật lý..."
mkdir -p logs work_minio output models
chmod -R 777 logs work_minio output models

# 2. KHỞI TẠO FILE BIẾN MÔI TRƯỜNG (.env)
echo "--> [2/5] Kiểm tra cấu hình môi trường (.env)..."
if [ ! -f .env ]; then
    echo "    Chưa có file .env, tiến hành copy từ .env.example..."
    cp .env.example .env
    echo "    ⚠️ Bạn nhớ mở file .env để sửa thông tin kết nối MinIO của máy khách hàng nếu cần nhé!"
else
    echo "    File .env đã tồn tại."
fi

# 3. NẠP DOCKER IMAGE OFFLINE
echo "--> [3/5] Đang nạp Docker image cho hệ thống AI và MinIO..."
if [ -f "images/pdf-splitter-latest.tar" ]; then
    docker load -i images/pdf-splitter-latest.tar
else
    echo "[LỖI] Không tìm thấy file images/pdf-splitter-latest.tar! Vui lòng kiểm tra lại."
    exit 1
fi

if [ -f "images/minio-latest.tar" ]; then
    echo "    Đang nạp Docker image cho MinIO..."
    docker load -i images/minio-latest.tar
else
    echo "⚠️  [CẢNH BÁO] Không tìm thấy file images/minio-latest.tar. MinIO có thể không khởi động được!"
fi

# 4. CẤU HÌNH OLLAMA MODEL OFFLINE (Bằng định dạng GGUF)
echo "--> [4/5] Khôi phục mô hình ngôn ngữ qwen2.5:7b vào máy chủ..."
if [ -f "models/qwen2.5-7b-instruct.gguf" ] && [ -f "models/Modelfile" ]; then
    if command -v ollama &> /dev/null; then
        cd models
        # Tạo model trong hệ thống Ollama cục bộ của khách hàng
        ollama create qwen2.5:7b -f ./Modelfile
        cd ..
        echo "    Đã nạp thành công mô hình AI nội bộ."
    else
        echo "    [LỖI] Máy chủ chưa cài đặt phần mềm Ollama, không thể nạp file GGUF!"
    fi
else
    echo "    [BỎ QUA] Không tìm thấy file GGUF hoặc Modelfile trong thư mục models/."
    echo "    (Vui lòng đảm bảo Ollama trên máy chủ đã có sẵn model qwen2.5:7b)."
fi

# 5. KHỞI ĐỘNG HỆ THỐNG VÀ KIỂM TRA
echo "--> [5/5] Đang khởi động AI API và Worker..."
docker compose up -d

echo "========================================================"
echo " Đang chờ hệ thống khởi động để kiểm tra API (5 giây)..."
sleep 5

echo " Kiểm tra Healthcheck API tại cổng 8090:"
HEALTH_STATUS=$(curl -s http://localhost:8090/health || echo "Failed to connect")

if [[ "$HEALTH_STATUS" == *"\"ok\": true"* || "$HEALTH_STATUS" == *"\"ok\":true"* ]]; then
    echo "--> [THÀNH CÔNG] API đã phản hồi: $HEALTH_STATUS"
else
    echo "--> [CẢNH BÁO] API chưa phản hồi chuẩn. Hãy dùng lệnh 'docker compose logs api' để kiểm tra!"
fi

echo "========================================================"
echo " HOÀN TẤT CÀI ĐẶT PHÂN HỆ AI!"
echo "========================================================"
