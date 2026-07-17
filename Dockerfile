FROM python:3.12-slim

WORKDIR /app

# ติดตั้ง deps ก่อน (ใช้ layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# โค้ดแอป
COPY app/ /app/

# เก็บผลลัพธ์ไว้ที่ /data (mount volume)
ENV OUTPUT_DIR=/data \
    SA_FILE=/secrets/sa.json \
    TZ=Asia/Bangkok \
    CRAWL_CRON="0 2 * * *" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
