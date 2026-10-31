FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn
COPY server.py .
EXPOSE 8090
CMD ["python", "server.py"]
