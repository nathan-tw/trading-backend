FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=app.py

# 使用 shell form 的 CMD 來支援多個指令串接 (&&)
# 1. python manage.py db upgrade: 執行資料庫遷移
# 2. gunicorn -w 1 -b 0.0.0.0:5000 'app:create_app()': 啟動生產環境 Server (使用 Factory Pattern)
CMD sh -c "python manage.py db upgrade && gunicorn -w 1 -b 0.0.0.0:5000 'app:create_app()'"
