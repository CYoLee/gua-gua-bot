FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# 安裝必要套件
RUN apt-get update && apt-get install -y \
    build-essential \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libgtk-3-0 \
    curl \
    wget \
    ca-certificates \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

# 手動安裝 Chromium 取代 playwright full 套件
RUN curl -LO https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt-get install -y ./google-chrome-stable_current_amd64.deb && \
    rm google-chrome-stable_current_amd64.deb

# 建立工作資料夾
WORKDIR /app

# 安裝 Python 套件
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# 複製專案其他檔案
COPY . .

CMD ["python", "redeem_web.py"]
