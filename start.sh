#!/bin/bash
set -e

PORT="${PORT:-8000}"
echo "========================================================"
echo " Starting Selaras Unified Service on port ${PORT}..."
echo "========================================================"

# Substitute PORT into nginx configuration
sed "s/PORT_PLACEHOLDER/${PORT}/g" /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Start FastAPI backend in background
echo "Starting FastAPI backend on 127.0.0.1:8000..."
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 &
FASTAPI_PID=$!

# Start Streamlit dashboard in background
echo "Starting Streamlit dashboard on 127.0.0.1:8501 (subpath /dashboard)..."
streamlit run dashboard/app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.baseUrlPath dashboard \
    --server.headless true \
    --theme.base light &
STREAMLIT_PID=$!

# Handler to gracefully stop background processes
cleanup() {
    echo "Stopping background services..."
    kill -TERM "$FASTAPI_PID" "$STREAMLIT_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start Nginx in foreground
echo "Starting Nginx reverse proxy on port ${PORT}..."
nginx -g "daemon off;" &
NGINX_PID=$!

# Wait for any process to exit
wait -n "$FASTAPI_PID" "$STREAMLIT_PID" "$NGINX_PID"
