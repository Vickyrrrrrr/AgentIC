# =============================================================================
# Stage 1: Frontend build
# =============================================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci --prefer-offline
COPY web/ ./
RUN npm run build

# =============================================================================
# Stage 2: Download OSS CAD Suite (separate cached layer)
# Update OSS_CAD_VERSION to upgrade ALL EDA tools in one place:
#   yosys, sby (SymbiYosys), eqy, verilator, iverilog, vvp, sv2v, nextpnr, ...
#
# Asset naming convention on GitHub releases:
#   oss-cad-suite-linux-x64-YYYYMMDD.tgz      (AMD64 / x86_64)
#   oss-cad-suite-linux-arm64-YYYYMMDD.tgz     (ARM64 / Oracle A1 Ampere)
# =============================================================================
FROM ubuntu:22.04 AS oss-cad-downloader
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates xz-utils && \
    rm -rf /var/lib/apt/lists/*

ARG OSS_CAD_VERSION=2025-04-06
# TARGETARCH is set automatically by Docker buildx: "amd64" or "arm64"
ARG TARGETARCH

# Map Docker TARGETARCH -> OSS CAD Suite filename suffix
#   amd64 -> x64      (NOT x86_64 — that path does not exist in the releases)
#   arm64 -> arm64
RUN ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x64") && \
    DATESTR=$(echo $OSS_CAD_VERSION | tr -d '-') && \
    curl -fsSL \
    "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_VERSION}/oss-cad-suite-linux-${ARCH}-${DATESTR}.tgz" \
    -o /tmp/oss-cad-suite.tgz && \
    tar -xzf /tmp/oss-cad-suite.tgz -C /opt && \
    rm /tmp/oss-cad-suite.tgz

# =============================================================================
# Stage 3: Python application runtime
# =============================================================================
FROM python:3.11-slim

# Minimal OS dependencies ONLY.
# NO apt-get yosys / verilator / iverilog — OSS CAD Suite is the single source.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    docker.io \
    ca-certificates \
    fontconfig \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Copy the full OSS CAD Suite from the downloader stage.
# Binaries are root-owned with 755 permissions from the tarball — no chown needed.
# appuser only needs read + execute access, which is already set.
COPY --from=oss-cad-downloader /opt/oss-cad-suite /opt/oss-cad-suite

ENV OSS_CAD_SUITE_HOME=/opt/oss-cad-suite
ENV PATH="/opt/oss-cad-suite/bin:${PATH}"

# Disable telemetry
ENV CREWAI_TRACING_ENABLED=false
ENV CREWAI_DISABLE_TELEMETRY=true
ENV OTEL_SDK_DISABLED=true

WORKDIR /app
ENV PYTHONPATH=/app
ENV PDK_ROOT=/app/pdk

# Install Python dependencies using system Python.
# DO NOT use /opt/oss-cad-suite/bin/python3 — it has a broken OpenSSL library.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir 'apscheduler>=3.10.0'

# Copy application source
COPY . .
COPY --from=frontend-builder /app/web/dist /app/web/dist

# Runtime directories + non-root user in one layer
# Only /app needs to be owned by appuser — OSS CAD Suite is read/exec only
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/designs /app/artifacts /app/pdk && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# Use gunicorn with uvicorn workers for production stability
CMD ["gunicorn", "server.api:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:7860"]
