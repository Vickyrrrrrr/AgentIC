# Deploying AgentIC Backend on Oracle Cloud Free Tier (Docker)

To host the backend API securely and permanently while relying on Vercel for the frontend, Oracle Cloud's "Always Free" tier is an excellent choice. Since this project natively supports Docker, we will deploy it fully containerized.

## 1. Instance Selection
Oracle offers two Free Tier instances:
- AMD Micro (x86_64): 1 GB RAM (⚠️ **DO NOT USE**: Will crash with Out-Of-Memory errors).
- **Ampere A1 (ARM64)**: Up to 4 OCPUs and 24 GB RAM (✅ **HIGHLY RECOMMENDED**).

## 2. Setting Up the Oracle VM (Ampere A1)
When creating your instance in the Oracle Cloud Console, ensure you select the exact parameters below to guarantee it remains in the **Always Free** tier and does not charge you:

- **Image:** Select **Ubuntu 22.04** or **Ubuntu 22.04 Minimal** (aarch64).
- **Shape:** Click "Change Shape", select the **Ampere** series, and choose **VM.Standard.A1.Flex**. Ensure the "Always Free Eligible" label is visible.
- **OCPUs & RAM:** Drag the sliders to max out the free tier: **4 OCPUs** and **24 GB RAM**.
- **Networking:** Ensure "Assign a public IPv4 address" is checked so Vercel can reach the backend.
- **Boot Volume:** Oracle provides up to 200 GB of free block storage across your account. You can check "Specify a custom boot volume size" and set it to **100 GB** to give Docker and OpenLane plenty of space.
- **SSH Keys:** Don't forget to save your private SSH key before clicking create, or you won't be able to log in!

*Note: Oracle might sometimes show a non-zero "Estimated Cost" on the summary screen depending on your region's display taxes, but as long as your OCPUs (≤4), RAM (≤24GB), and total Boot Volume (≤200GB) are within the free usage limits, your actual monthly bill will be 0.00.*

### Step 2.1: Open the Networking Firewall (Crucial)
Oracle's Virtual Cloud Network (VCN) blocks all incoming traffic by default, preventing Vercel from reaching your backend.
1. Go to your Instance -> Initial VCN -> **Security Lists** -> Default Security List.
2. Click **Add Ingress Rule**:
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: `TCP`
   - Destination Port Range: `7860`

### Step 2.2: Open Ubuntu's Internal Firewall (iptables)
SSH into your Oracle instance and open the port at the OS level:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 7860 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Clone and Run Docker Setup
Because you are using the Ampere (ARM64) architecture, Docker will automatically pull and build the ARM64 versions of the Python base image and EDA packages defined in the `Dockerfile`. 

SSH into your Oracle VM and run the setup script to install Docker and build the container:
```bash
git clone https://github.com/Vickyrrrrrr/AgentIC.git
cd AgentIC
chmod +x scripts/setup_oracle.sh
./scripts/setup_oracle.sh
```

## 4. Environment Variables
Ensure you have an `.env` file in the root of the repository before bringing the container up. It should contain:
```env
CORS_ORIGIN=https://your-frontend-project.vercel.app
OPENLANE_ROOT=/home/ubuntu/MVP/OpenLane
```

## 5. Start the Backend Container
Bring up the backend using Docker Compose:
```bash
docker-compose up -d --build
```
This maps port `7860` and mounts your persistent `designs/` and `artifacts/` securely on the fast Oracle SSD.

## 6. Update Vercel Frontend
Change your frontend environment variable to point to the Public IP of your Oracle Instance:
`VITE_API_URL = "http://<ORACLE_PUBLIC_IP>:7860"`
Redeploy Vercel.

## 7. Frequently Asked Questions

### Troubleshooting: "Out of capacity for shape VM.Standard.A1.Flex"
If you get an "Out of capacity" error when trying to create your Ampere A1 instance, it means Oracle's data center in your region currently has no free ARM servers available. This is a very common issue with the Always Free tier. 

**How to bypass this:**
1. **Try different domains:** If your region has AD-2 or AD-3, selecting them might work. Ensure "Fault Domain" is left blank.
2. **Upgrade to Pay-As-You-Go (Recommended):** If you add a credit card and upgrade your account to "Pay As You Go," Oracle gives your account provisioning priority. **You will still not be charged** as long as you keep the sliders at or below the free limits (4 OCPUs, 24 GB RAM, 200 GB Storage). "Always Free" resources remain free even on paid accounts.
3. **Try later:** Capacity opens up dynamically when other users delete their instances. Trying during off-peak hours can sometimes work.

**Do I have to make my repository public?**
No, you do not have to make your repository public. Oracle Cloud allows you to host private instances. If your repo is private, you can securely clone it to your Oracle VM using an **SSH Key** or a **GitHub Personal Access Token (PAT)**:
```bash
git clone https://<YOUR_GITHUB_PAT>@github.com/Vickyrrrrrr/AgentIC.git
```

If you prefer SSH, add your Oracle VM public key to GitHub and clone with:
```bash
git clone git@github.com:Vickyrrrrrr/AgentIC.git
```

If you use GitHub CLI on the Oracle VM, you can also do:
```bash
gh auth login
gh repo clone Vickyrrrrrr/AgentIC
```

**How does the backend consume my LLM API keys?**
Because you are deploying via `docker-compose`, the backend securely extracts your API keys directly from your local `.env` file on the Oracle VM. **Never commit your `.env` file to your repository.**

When configuring your server via SSH, simply create your `.env` file in the project root:
```bash
nano .env
```
And paste your keys natively:
```env
# Multi-LLM Role Engine Keys
NVIDIA_API_KEY=nvapi-your-key-here
GLM_API_KEY=your-zhipu-key-here
GROQ_API_KEY=gsk_your-groq-key-here

# Integration Links
CORS_ORIGIN=https://agent-ic.vercel.app
OPENLANE_ROOT=/home/ubuntu/MVP/OpenLane
```
When you run `docker-compose up -d --build`, Docker will automatically pull these secrets from the `.env` file and securely inject them into the `agentic_local` backend container at OS-level, ensuring the multi-agent orchestrator can query the LLMs securely.
