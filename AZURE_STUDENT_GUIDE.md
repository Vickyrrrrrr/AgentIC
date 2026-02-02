# ☁️ Azure Student Developer Pack Guide for AgentIC

Since your local PC is overheating, moving to Azure is the perfect solution. With the **Microsoft Azure Student Pack**, you get **$100 credit** and free access to specific services.

This guide will help you create a **Virtual Machine (VM)** to run OpenLane and AgentIC without burning your laptop.

## 1. Create the Virtual Machine (VM)
The "hectic" part is the OpenLane build process (compiling 30,000 transistors!).

1.  Log in to the [Azure Portal](https://portal.azure.com).
2.  Click **"Create a resource"** > **"Virtual Machine"**.
3.  **Basics Tab**:
    *   **Subscription**: Azure for Students.
    *   **Resource Group**: Create new (e.g., `rg-agentic`).
    *   **Virtual Machine Name**: `vm-agentic`.
    *   **Region**: Pick one close to you (e.g., East US).
    *   **Image**: **Ubuntu Server 22.04 LTS - x64 Gen2**.
    *   **Size**: **Standard D4s v3** (4 vCPUs, 16 GiB memory).
        *   *Note:* This costs about ~$0.18/hour. Since you have $100 credits, you can run this for ~500 hours.
        *   *Important:* Remember to **STOP** the VM when you aren't using it to save credits!
4.  **Administrator Account**:
    *   **Authentication type**: SSH public key.
    *   **Username**: `azureuser`.
    *   **Key source**: Generate new key pair.
5.  **Review + create** > **Create**.
    *   Download the private key (`.pem` file) when prompted. Keep it safe!

## 2. Connect to the Cloud
1.  Open your local terminal (Git Bash or WSL).
2.  Move the key to a safe place and secure it:
    ```bash
    mv ~/Downloads/vm-agentic_key.pem ~/.ssh/
    chmod 400 ~/.ssh/vm-agentic_key.pem
    ```
3.  Connect (replace `YOUR_VM_IP` with the Public IP from Azure Portal):
    ```bash
    ssh -i ~/.ssh/vm-agentic_key.pem azureuser@YOUR_VM_IP
    ```

## 3. Install the Environment (One-Time Setup)
Copy and paste this block into your Azure SSH terminal to install everything:

```bash
# Update and Install Docker
sudo apt-get update
sudo apt-get install -y docker.io python3-pip python3-venv git make
sudo usermod -aG docker $USER
newgrp docker

# Setup OpenLane
git clone https://github.com/The-OpenROAD-Project/OpenLane.git ~/OpenLane
cd ~/OpenLane
# This download is heavy (~2GB), but it runs on Azure's fast internet!
make pull-openlane 

# Get your AgentIC code
# (You can clone your repo, or we create a fresh one)
mkdir -p ~/AgentIC
```

## 4. Solving the "Thinking" Heat (The LLM)
Running Ollama (DeepSeek) locally is what heats up your GPU. On an Azure CPU VM, it will be slow.
**Recommendation:** Use the **DeepSeek API** instead. It costs pennies and offloads the "brain" work to DeepSeek's servers.

1.  Get an API Key from [platform.deepseek.com](https://platform.deepseek.com).
2.  On the Azure VM, export it:
    ```bash
    export OPENAI_API_KEY="sk-..."  # Your DeepSeek Key
    export OPENAI_API_BASE="https://api.deepseek.com"
    export LLM_MODEL="deepseek-chat"
    ```

## 5. Running the Flow
Now, your laptop stays cool while Azure does the work.

```bash
# On Azure VM
cd ~/AgentIC
python3 main.py build --name cloud_chip --desc "An 8-bit counter"
```

## 6. Retrieving the Result
When finished, download the GDS file to your laptop to view it:

```bash
# On Local Laptop
scp -i ~/.ssh/vm-agentic_key.pem azureuser@YOUR_VM_IP:~/AgentIC/cloud_chip.gds ./
```
