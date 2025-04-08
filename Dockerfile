FROM python:3.11-slim

WORKDIR /app

# 安裝系統相依套件：Chromium + Tesseract
RUN apt-get update && apt-get install -y \
    chromium-driver \
    chromium \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# 設定環境變數供 Selenium 使用 headless chrome
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
