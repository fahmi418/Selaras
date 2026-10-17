FROM python:3.11-slim

WORKDIR /app

# Install build deps and nginx for unified proxying
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libgomp1 nginx libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

# Setup nginx template and permissions
COPY nginx.conf.template /etc/nginx/nginx.conf.template
RUN chmod +x /app/start.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \n    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)" || exit 1

CMD ["/bin/bash", "/app/start.sh"]

