#Dockerfile
# 使用官方python image
FROM python:3.10-slim

# 安裝系統套件
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    fonts-noto-cjk \
    fonts-dejavu-core \
    tesseract-ocr \
    libglib2.0-0 \
    libnss3 \
    libgl1 \
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
    --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 工作目錄
WORKDIR /app

# 複製專案檔案
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 安裝playwright瀏覽器
RUN playwright install --with-deps

# 預設啟動
CMD ["uvicorn", "redeem_web:app", "--host", "0.0.0.0", "--port", "8080"]
