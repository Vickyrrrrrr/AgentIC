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
    && rm -rf /var/lib/apt/lists/*

# Install SymbiYosys from source
RUN git clone --depth 1 https://github.com/YosysHQ/sby /tmp/sby \
    && cd /tmp/sby && make install \
    && rm -rf /tmp/sby

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir uvicorn[standard] fastapi && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# HuggingFace Spaces runs as non-root user 1000
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

CMD ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "7860"]
