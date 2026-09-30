FROM python:3.11-slim

WORKDIR /app

# Install build deps and nginx for unified proxying
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libgomp1 nginx libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# Setup nginx template and permissions
COPY nginx.conf.template /etc/nginx/nginx.conf.template
RUN chmod +x /app/start.sh

EXPOSE 8000

CMD ["/bin/bash", "/app/start.sh"]

