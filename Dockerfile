# 使用 Python slim 版本為基底
FROM python:3.10-slim

EXPOSE 8080

# 安裝系統與必要套件（新增編譯相關）
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    build-essential \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    fonts-noto-cjk \
    fonts-dejavu-core \
    tesseract-ocr \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libxss1 \
    libasound2 \
    libxtst6 \
    libxrandr2 \
    libatk-bridge2.0-0 \
    libcups2 \
    libatk1.0-0 \
    libgtk-3-0 \
    ca-certificates \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# 安裝 Node.js（Playwright 需要）
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm

# 安裝 Playwright 至暫存資料夾（避免 cache 錯誤）
RUN mkdir -p /tmp/pw && cd /tmp/pw && \
    npm init -y && \
    npm install --no-audit --no-fund --legacy-peer-deps playwright && \
    npx playwright install --with-deps && \
    rm -rf /tmp/pw

# 建立工作資料夾
WORKDIR /app

# 安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip show opencv-python-headless && \
    pip freeze > installed.txt && \
    cat installed.txt

# 複製專案其他檔案
COPY . .

# 啟動命令
CMD ["python", "redeem_web.py"]