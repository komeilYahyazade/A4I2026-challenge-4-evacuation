# readiness-web: React build served by a small FastAPI app that forwards /v1 to readiness-api.
FROM node:22.12.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY server.py .
COPY --from=build /app/dist ./dist
ENV PORT=8080
CMD exec uvicorn server:app --host 0.0.0.0 --port ${PORT}
