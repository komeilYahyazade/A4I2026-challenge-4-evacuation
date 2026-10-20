# readiness-api: FastAPI + ADK. Built from the repo root (needs agent/, api/, contract/, sql/).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY agent/ agent/
COPY api/ api/
COPY contract/ contract/
COPY sql/ sql/
ENV PORT=8080
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
