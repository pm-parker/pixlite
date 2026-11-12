# Reproducible clean-checkout build (Issue #3).
# Build from repo root:  docker build -f docker/Dockerfile -t pixlite .
# Run (models are downloaded automatically on first start into /data/models):
#   docker run -p 8900:8900 -e PIXLITE_TOKEN=<strong-token> \
#     -v pixlite-data:/data -v /path/to/photos:/photos pixlite
# or bake models in with --build-arg WITH_MODELS=1 (downloads + sha256-verifies at build time).

# ---- stage 1: frontend ----
FROM node:22-slim AS web
WORKDIR /src/webapp
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webapp/ ./
# vite outDir = ../server/web
RUN npm run build && test -f /src/server/web/index.html

# ---- stage 2: runtime ----
FROM python:3.12-slim
ARG WITH_MODELS=0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine/ engine/
COPY server/ server/
COPY presets/ presets/
COPY tools/ tools/
COPY fnpack/etc/lut/ lut/
COPY fnpack/manifest fnpack/manifest
COPY --from=web /src/server/web/ server/web/
RUN if [ "$WITH_MODELS" = "1" ]; then python tools/download_models.py; fi
ENV PIXLITE_LUT_DIR=/app/lut/Filters \
    PIXLITE_WORK_DIR=/data/work \
    PIXLITE_ALLOWED_ROOTS=/data:/photos \
    PIXLITE_MODELS_DIR=/data/models
RUN mkdir -p /data/work /photos
# smoke: app imports and static assets are present
RUN python -c "import server.api.main as m, os; assert os.path.isdir(m.WEB_DIR)"
EXPOSE 8900
# server.run = uvicorn on TCP (+ optional unix socket via PIXLITE_GATEWAY_SOCKET)
CMD ["python","-m","server.run","--host","0.0.0.0","--port","8900"]
