FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY supervisor/frontend/package*.json ./
RUN npm ci --registry=https://registry.npmjs.org
COPY supervisor/frontend/ ./
RUN npm run build
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/cdn/supervisor
COPY supervisor/ ./
COPY agent/ /opt/cdn/agent/
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home cdn
COPY --from=frontend /build/dist ./frontend/dist
USER 10001
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips=127.0.0.1","--no-access-log","--log-level","warning"]
