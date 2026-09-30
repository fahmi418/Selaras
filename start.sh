#!/bin/bash
set -e

PORT="${PORT:-8080}"
echo "========================================================"
echo " Starting Selaras Unified Service on port ${PORT}..."
echo "========================================================"

# Prepare listen directives so Nginx listens on $PORT, 8000, and 8080
LISTEN_DIRECTIVES="listen ${PORT};"
if [ "${PORT}" != "8000" ]; then
    LISTEN_DIRECTIVES="${LISTEN_DIRECTIVES}
        listen 8000;"
fi
if [ "${PORT}" != "8080" ] && [ "${PORT}" != "8000" ]; then
    LISTEN_DIRECTIVES="${LISTEN_DIRECTIVES}
        listen 8080;"
fi

# Substitute LISTEN_DIRECTIVES into nginx configuration
awk -v r="${LISTEN_DIRECTIVES}" '{gsub(/LISTEN_DIRECTIVES/, r)}1' /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Start FastAPI backend in background on 127.0.0.1:8001
echo "Starting FastAPI backend on 127.0.0.1:8001..."
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8001 &
FASTAPI_PID=$!

# Start Streamlit dashboard in background on 127.0.0.1:8501
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
echo "Starting Nginx reverse proxy..."
nginx -g "daemon off;" &
NGINX_PID=$!

# Wait for any process to exit
wait -n "$FASTAPI_PID" "$STREAMLIT_PID" "$NGINX_PID"
