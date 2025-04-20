# Dockerfile
FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

WORKDIR /app

# 安裝中文字型與 OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# 複製專案檔案
COPY . .

# 安裝 Python 套件與 Playwright 瀏覽器
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
RUN playwright install --with-deps

# 環境變數設定
RUN PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium
ENV PLAYWRIGHT_CHROMIUM_ARGS="--no-sandbox --disable-dev-shm-usage"
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# 執行主程式
CMD ["python", "redeem_web.py"]
