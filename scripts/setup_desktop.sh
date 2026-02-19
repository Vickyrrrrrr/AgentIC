#!/bin/bash

echo "========================================"
echo "    AgentIC Desktop Server Setup"
echo "========================================"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 could not be found."
    echo "   Please install Python 3.10 or newer."
    exit 1
fi

echo "✅ Python 3 found."

# Create a virtual environment
echo "📦 Creating virtual environment 'venv'..."
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install vLLM
echo "⬇️  Installing vLLM (this may take a while)..."
# We specifically install vllm for fast inference
pip install vllm

# Install HuggingFace transfer for faster downloads
pip install hf_transfer
export HF_HUB_ENABLE_HF_TRANSFER=1

echo ""
echo "========================================"
echo "✅ Setup Complete!"
echo "========================================"
echo ""
echo "To run the server:"
echo "1. source venv/bin/activate"
echo "2. python launch_server.py --model Qwen/Qwen2.5-Coder-32B-Instruct"
echo ""
