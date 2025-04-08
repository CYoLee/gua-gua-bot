# Dockerfile

FROM python:3.11-slim

WORKDIR /app

# 複製程式碼進容器
COPY . .

# 安裝 Python 套件
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# 將 .env 複製進容器（選用，Cloud Run 上建議用環境變數配置）
COPY .env .env

# 設定啟動指令（Flask 預設用環境變數讀取）
CMD ["python", "main.py"]
