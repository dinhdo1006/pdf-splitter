# ── Dockerfile — PDF Splitter (PaddlePaddle GPU, CUDA 12.9, RTX 50-series) ──
# Base: CUDA 12.9 runtime + cuDNN trên Ubuntu 24.04
FROM nvidia/cuda:12.9.0-cudnn-runtime-ubuntu24.04

# Tránh tzdata prompt
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

# Ubuntu 24.04 dùng PEP 668 — cho phép pip cài system-wide trong container
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# ── Cài Python 3.12 + các gói hệ thống cần thiết ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3-pip \
    python3.12-venv \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    wget \
    curl \
    ca-certificates \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Thư mục làm việc ──
WORKDIR /app

# ── Cài pip mới qua get-pip.py (tránh xung đột pip Debian) ──
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3

# ── Cài thư viện thường (không bao gồm paddle) ──
COPY requirements.txt .
RUN pip install --no-cache-dir \
    pymupdf \
    paddleocr \
    opencv-python-headless \
    numpy \
    rapidfuzz \
    unidecode \
    loguru \
    tqdm \
    minio \
    python-dotenv \
    fastapi \
    "uvicorn[standard]" \
    transformers \
    torch \
    Pillow \
    sentencepiece

# ── Cài PaddlePaddle GPU 3.3.0 CUDA 12.9 (RTX 50-series / Blackwell sm_120) ──
RUN pip install --no-cache-dir \
    paddlepaddle-gpu==3.3.0 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cu129/

# ── Fix xung đột NCCL giữa Paddle và PyTorch ──
RUN pip install --no-cache-dir \
    --force-reinstall --no-deps \
    nvidia-nccl-cu13==2.29.7

# ── Copy toàn bộ source code vào image ──
COPY . .

# ── Tạo thư mục cần thiết ──
RUN mkdir -p logs output work_minio

# ── Biến môi trường mặc định (override bằng .env hoặc docker-compose) ──
ENV SPLITTER_API_HOST=0.0.0.0
ENV SPLITTER_API_PORT=8090
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8090
