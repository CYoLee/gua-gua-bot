FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps
COPY . .
CMD ["python", "redeem_web.py"]
