FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGENT_MODE=sandbox
WORKDIR /opt/cdn/agent
COPY agent/ ./
RUN pip install --no-cache-dir . && useradd --uid 10002 --create-home cdn-agent && mkdir -p /tmp/cdn-sandbox && chown 10002:10002 /tmp/cdn-sandbox
USER 10002
EXPOSE 9443
CMD ["python","-m","app.main"]
