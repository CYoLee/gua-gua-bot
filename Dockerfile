# Dockerfile
FROM mcr.microsoft.com/playwright/python:latest

WORKDIR /app

# 安裝中文字型與 tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# 安裝 Playwright 的瀏覽器依賴
RUN playwright install --with-deps

# 設定為無 sandbox 模式（Cloud Run 禁止 sandbox）
ENV PLAYWRIGHT_BROWSERS_PATH=0
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
