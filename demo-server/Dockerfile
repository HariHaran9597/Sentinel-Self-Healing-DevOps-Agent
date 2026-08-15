FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY server.py .
RUN mkdir -p /var/log/app

ENV LOG_PATH=/var/log/app/app.log HOST=0.0.0.0 PORT=8080
EXPOSE 8080

CMD ["python", "server.py"]
