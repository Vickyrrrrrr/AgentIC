# Installation & Portability Guide

Use this guide to set up **AgentIC** on a new machine.

## 1. System Requirements
*   **Operating System**: Linux (Ubuntu 20.04/22.04 LTS) or Windows with **WSL2** (Ubuntu).
    *   *Note: Pure Windows is not supported due to Electronic Design Automation (EDA) tool dependencies.*
*   **Memory**: 8GB RAM minimum (16GB recommended for Physical Design).
*   **Disk Space**: ~10GB (mostly for Docker images and PDKs).

## 2. Core Dependencies
Install the required system tools before setting up the Python environment.

### Ubuntu / Debian / WSL2:
```bash
sudo apt update
sudo apt install -y git make python3 python3-venv python3-pip
sudo apt install -y iverilog build-essential
```

### Docker (Critical for OpenLane)
AgentIC uses OpenLane (running in Docker) to turn Verilog into GDSII layouts.
1.  **Install Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux).
2.  **Verify installation**:
    ```bash
    docker run hello-world
    ```
3.  **Linux/WSL2 users**: Ensure your user is in the docker group so you don't need `sudo`:
    ```bash
    sudo usermod -aG docker $USER
    # Log out and log back in for this to take effect
    ```

## 3. Python Environment Setup

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/Vickyrrrrrr/AgentIC.git
    cd AgentIC
    ```

2.  **Create and Activate Virtual Environment**:
    ```bash
    python3 -m venv agentic_env
    source agentic_env/bin/activate
    ```

3.  **Install Python Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install GDSTK (Layout Viewer)**:
    If `pip install gdstk` fails, you may need cmake:
    ```bash
    sudo apt install cmake
    pip install gdstk
    ```

## 4. Configuration (.env)

You need to provide your LLM API keys.
1.  Create a file named `.env` in the root `AgentIC` directory.
2.  Add your keys (example for Groq):
    ```ini
    # .env file
    OPENAI_API_BASE=https://api.groq.com/openai/v1
    OPENAI_API_KEY=gsk_your_groq_api_key_here
    OPENAI_MODEL_NAME=llama-3.3-70b-versatile
    ```

## 5. Verification
To ensure everything is working:

1.  **Test the Agent Logic**:
    ```bash
    python3 main.py build --name test_counter --desc "A simple 4-bit up counter"
    ```
2.  **Test the Web UI**:
    ```bash
    streamlit run app.py
    ```
