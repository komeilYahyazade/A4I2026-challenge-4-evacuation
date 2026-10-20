# Agent image for `gcloud run deploy --source .` (see deploy/deploy_agent.sh).
# Serves agent/server.py: the ADK API (/apps/..., /run_sse, dev UI) plus the deterministic /api routes.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    GOOGLE_CLOUD_LOCATION=global \
    GOOGLE_GENAI_USE_VERTEXAI=True \
    GOOGLE_GENAI_USE_ENTERPRISE=True \
    AGENTS_DIR=/app/agents \
    PYTHONPATH=/app/agents

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir uv \
 && uv pip install --system --no-cache -r requirements.txt \
 && useradd --create-home --uid 10001 app

# The ADK agent loader treats every folder under AGENTS_DIR as an app, so only the agent goes there.
COPY agent/ ./agents/agent/
USER app
WORKDIR /app/agents

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn agent.server:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75"]
