# Cloud Migration Guide for AgentIC + OpenLane

This guide explains how to move your local AI-to-GDSII flow to a cloud provider (AWS, GCP, Azure, Lambda Labs, or RunPod).

## 1. Hardware Requirements
OpenLane and Local LLMs are resource-intensive. Do not use a "Free Tier" (t2.micro) instance.

*   **CPU**: 4 vCPUs minimum (8 recommended for Place & Route).
*   **RAM**: 16 GB minimum (32 GB recommended).
*   **Storage**: 50 GB SSD minimum (Docker images + PDKs take ~30GB).
*   **GPU**: NVIDIA T4 (16GB VRAM) or better recommended if running Ollama locally.

## 2. Setup Script (Ubuntu 22.04 / 24.04)
Run these commands on your new cloud instance to replicate your environment.

### A. Install System Dependencies
```bash
sudo apt-get update && sudo apt-get install -y \
    docker.io \
    python3-pip \
    python3-venv \
    git \
    make \
    curl \
    iverilog
    
# Add user to docker group (avoids sudo for docker)
sudo usermod -aG docker $USER
newgrp docker
```

### B. Install Ollama (for DeepSeek)
If you have a GPU, install the Nvidia Container Toolkit first. If CPU-only, just run:
```bash
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model
ollama pull deepseek-r1
```

### C. Clone & Setup Your Workspace
```bash
# Clone OpenLane (Infrastructure)
git clone https://github.com/The-OpenROAD-Project/OpenLane.git ~/OpenLane
cd ~/OpenLane
make pull-openlane  # Downloads the heavy Docker images

# Clone AgentIC (Your Logic)
# (Assuming you push your local code to a git repo first)
git clone <YOUR_GITHUB_REPO_URL> ~/AgentIC

# Setup Python Env
cd ~/AgentIC
python3 -m venv agentic_env
source agentic_env/bin/activate
pip install -r requirements.txt
```

## 3. Remote Access
To view your designs (GDSII files) or access the terminal easily, use VS Code Remote - SSH.

1.  In local VS Code: Install "Remote - SSH" extension.
2.  Connect to `ssh user@your-cloud-ip`.
3.  Open the folder `/home/user/AgentIC`.

## 4. Modified Flow for Cloud (Headless)
Since you won't have a GUI window for `klayout` or unnecessary graphics:

1.  Run the build command normally:
    ```bash
    python main.py build --name cloud_adder --desc "8-bit adder"
    ```
2.  **Viewing Results**: Do not try to open KLayout on the server. Instead, use the `scp` command to copy the final GDS file to your local machine:
    ```bash
    # Run this on your LOCAL machine
    scp user@your-cloud-ip:~/OpenLane/designs/cloud_adder/runs/agentrun/results/final/gds/cloud_adder.gds ./
    ```
