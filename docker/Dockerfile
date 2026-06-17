# ── Stage 1: Build frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

# npm mirror (override with --build-arg NPM_REGISTRY=... to use a mirror)
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci
COPY frontend/ .
COPY pyproject.toml /pyproject.toml
RUN npm run build

# ── Stage 2: Production image ──────────────────────────────────────────
FROM python:3.10-slim

# pip mirror (override with --build-arg PIP_INDEX_URL=... to use another mirror)
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
COPY python/ python/
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -e .

# Copy built frontend assets for platform mode
COPY --from=frontend-builder /build/dist /app/frontend/dist

# Copy project metadata and tools
COPY README.md LICENSE ./
COPY tools/ /app/tools/

# Default environment
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    FRONTEND_DIST=/app/frontend/dist

# Default: platform mode (FastAPI + frontend)
EXPOSE 8000
CMD ["uvicorn", "lockbot.backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
