FROM python:3.11-slim

# Install system dependencies and EDA tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    curl \
    libboost-dev \
    zlib1g-dev \
    libfftw3-dev \
    libreadline-dev \
    tcl-dev \
    pkg-config \
    verilator \
    iverilog \
    yosys \
    fontconfig \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Install SymbiYosys
RUN pip install --no-cache-dir symbiyosys 2>/dev/null || \
    (git clone --depth 1 https://github.com/YosysHQ/sby /tmp/sby \
     && cd /tmp/sby && make install && rm -rf /tmp/sby) || true

WORKDIR /app

ENV PYTHONPATH=/app

# Disable CrewAI telemetry — stops the 20s interactive prompt on every LLM call
ENV CREWAI_TRACING_ENABLED=false
ENV CREWAI_DISABLE_TELEMETRY=true
ENV OTEL_SDK_DISABLED=true

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir uvicorn[standard] fastapi && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir apscheduler>=3.10.0 'litellm[proxy]'

# Copy application code
COPY . .

# Runtime directories
RUN mkdir -p /app/designs /app/artifacts /app/pdk

ENV PDK_ROOT=/app/pdk

# HuggingFace Spaces runs as non-root user 1000
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

CMD ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "7860"]