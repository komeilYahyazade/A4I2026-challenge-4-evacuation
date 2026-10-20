# readiness-ui: Streamlit. Built from frontend/ (gcloud run deploy readiness-ui --source frontend).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py api_client.py attribution.py ./
ENV PORT=8080
CMD exec streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false
