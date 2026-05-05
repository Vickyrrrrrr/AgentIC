# =============================================================================
# Stage 1: Download OSS CAD Suite (separate cached layer)
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

# Use uname -m to detect the real host architecture at build time.
# ARG TARGETARCH is only set by `docker buildx` — legacy docker-compose
# v1.29 never populates it, causing the wrong tarball to be downloaded.
#
#   uname -m output -> OSS CAD Suite filename suffix
#   aarch64         -> arm64
#   x86_64          -> x64
RUN ARCH=$([ "$(uname -m)" = "aarch64" ] && echo "arm64" || echo "x64") && \
    DATESTR=$(echo $OSS_CAD_VERSION | tr -d '-') && \
    echo "Detected arch: $(uname -m) -> oss-cad-suite-linux-${ARCH}-${DATESTR}.tgz" && \
    curl -fsSL \
    "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_VERSION}/oss-cad-suite-linux-${ARCH}-${DATESTR}.tgz" \
    -o /tmp/oss-cad-suite.tgz && \
    tar -xzf /tmp/oss-cad-suite.tgz -C /opt && \
    rm /tmp/oss-cad-suite.tgz

# =============================================================================
# Stage 2: Python application runtime
# =============================================================================
FROM python:3.11-slim

# OS runtime dependencies:
#   perl         — verilator is a Perl wrapper (uses FindBin, File::Basename, etc.)
#   libgcc-s1    — shared lib required by iverilog / vvp ELF binaries
#   libstdc++6   — C++ stdlib required by yosys and other OSS CAD tools
#   curl         — health checks + runtime downloads
#   docker.io    — OpenLane container execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    perl \
    libgcc-s1 \
    libstdc++6 \
    curl \
    docker.io \
    ca-certificates \
    fontconfig \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Copy the full OSS CAD Suite from the downloader stage.
# Binaries are root-owned with 755 permissions from the tarball — no chown needed.
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


# Runtime directories + non-root user in one layer
# Pre-create ALL runtime dirs so appuser owns them inside the image.
# Volume mounts at runtime will overlay these but the ownership is
# already correct — no chown needed at container startup.
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/designs /app/artifacts /app/pdk /app/training && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# Use gunicorn with uvicorn workers for production stability
CMD ["gunicorn", "server.api:app", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:7860"]
