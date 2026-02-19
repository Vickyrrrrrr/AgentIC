# 🎓 Beginner's Guide: Using Your Desktop as an AI Brain

You want to use your powerful Desktop (for its GPU) to do the heavy thinking, while working comfortably on your Laptop. This guide explains how to connect them.

## 🧩 The Parts

Imagine this system like a **Remote Control Car**:
1.  **The Controller (Your Laptop)**: It has the buttons and the map (AgentIC). It tells the car where to go.
2.  **The Car (Your Desktop)**: It has the engine (GPU). It does the actual driving (thinking).
3.  **The Signal (Network)**: The connection between them.

**Crucially:** You cannot use an HDMI cable for this. You must use **Ethernet (LAN cable)** or **Wi-Fi**.

---

## 🛠️ Step 1: Set Up The Desktop (The Car)

Go to your **Desktop computer** for these steps.

### 1. Create a Folder
Open a terminal on your desktop and run:
```bash
mkdir ~/agentic-server
cd ~/agentic-server
```

### 2. Copy Files
You need to copy two files from your laptop's `AgentIC/scripts/` folder to this new folder on your desktop. You can email them to yourself, use a USB drive, or use `scp`.
- `launch_server.py`
- `setup_desktop.sh`

### 3. Install Requirements
Run the setup script to install the AI software (`vllm`):
```bash
bash setup_desktop.sh
```

### 4. Start the Engine
Run the server. Replace the model name with your preferred model if needed.
```bash
# First, activate the environment
source venv/bin/activate

# Launch the server (this will download the model first, which takes time!)
python launch_server.py --model Qwen/Qwen2.5-Coder-32B-Instruct
```
*Wait until you see "Application startup complete".*

---

## 🔍 Step 2: Find Desktop's Address

Your laptop needs to know *where* the desktop is on the network.
1.  Open a new terminal on the **Desktop**.
2.  Run: `hostname -I`
3.  You will see numbers like `192.168.1.50`. Write this down. This is your **Desktop IP**.

---

## 🎮 Step 3: Connect The Laptop (The Controller)

Go back to your **Laptop**.

1.  Open `AgentIC/src/agentic/config.py`.
2.  Find the `LOCAL_CONFIG` section.
3.  Change `base_url` to match your Desktop IP:

```python
LOCAL_CONFIG = {
    "model": "Qwen/Qwen2.5-Coder-32B-Instruct", # Must match what you ran on Desktop
    "base_url": "http://192.168.1.50:8000/v1",  # <--- REPLACE WITH DESKTOP IP
    "api_key": "EMPTY"                          # Key is not needed for local network
}
```

## ✅ Step 4: Test It

On your laptop, run a simple build command:
```bash
agentic build --prompt "A simple D-flip flop" --model Qwen/Qwen2.5-Coder-32B-Instruct
```

If it works, your laptop is successfully steering the desktop!
