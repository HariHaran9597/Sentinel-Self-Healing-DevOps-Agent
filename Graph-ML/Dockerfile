FROM python:3.11-slim AS base

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY models/ models/
COPY reports/ reports/
COPY src/fraudgraph/api/static/ static/

EXPOSE 8000
CMD ["uvicorn", "fraudgraph.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
