# 🚀 AgentIC: Public Launch & Distribution Guide

This document is your master blueprint for selling, distributing, and marketing the AgentIC binary without exposing your source code. 

---

## 🏗️ Phase 1: Where to Host & Sell the Executable

Because you are selling a compiled desktop application that requires a License Key, **Lemon Squeezy** or **Gumroad** are your best options. They handle both the payment AND the file hosting.

1. **Create a Lemon Squeezy Account:** Go to [lemonsqueezy.com](https://www.lemonsqueezy.com/) and register.
2. **Create a New Product:**
   * **Name:** AgentIC Local Engine
   * **Pricing:** Set your price (e.g., $49/month or $199 lifetime).
   * **Files:** Upload the `main` executable from your `secure_build/dist/` folder (rename it to `agentic-linux` or `agentic-windows.exe` before uploading).
   * **License Keys:** Turn ON "Generate License Keys". Set it to 1 or 2 activations per purchase.
3. **Get Your Checkout Link:** Lemon Squeezy will give you a shareable Checkout URL. This is the link you will put everywhere.

---

## 🛡️ Phase 2: Creating the Public GitHub Repository

**CRITICAL:** Do NOT make your current `AgentIC` private repository public! You will create a **brand new**, empty public repository just for marketing and documentation.

1. Go to GitHub and create a new repository called `AgentIC-Public` (or just `AgentIC` if your private repo has a different name).
2. **Do not upload any Python code!** 
3. Only upload this `README.md` (see template below) and optionally a `.env.example`.

---

## 📢 Phase 3: The Public `README.md` Template
*Copy everything below the line and use it as the README.md for your new PUBLIC repository.*

---

<hr>
<br>

<div align="center">
  <h1>⚡ AgentIC Engine</h1>
  <p><b>The Limitless AI-Driven Silicon Compiler. From Natural Language to Fabrication-Ready GDSII.</b></p>
  <br>
  <a href="YOUR_LEMON_SQUEEZY_CHECKOUT_LINK"><b>🛒 Purchase License & Download</b></a> •
  <a href="#-quick-start"><b>📚 Documentation</b></a> •
  <a href="YOUR_WEBSITE_OR_TWITTER"><b>🌐 Website</b></a>
</div>

<br>

## 🧠 What is AgentIC?

**AgentIC** is a proprietary physics-aware AI hardware design suite. It seamlessly bridges the massive gap between natural language intention and fabrication-ready GDSII chip layouts. 

Instead of manually writing thousands of lines of Verilog and debugging manual synthesis loops, you simply describe your chip. AgentIC handles the logic generation, hierarchical recursive verification, timing constraints, and physical routing.

### ✨ Features
* **Recursive Bottom-Up Parsing:** Capable of generating massive Out-of-Order processors or crypto-accelerators by autonomously breaking them down into digestible sub-graphs.
* **Automated physical bounds:** Connects directly to OpenLane to verify your design works in target foundry nodes like Sky130.
* **100% Local Privacy:** The executable runs entirely on your local machine. Your proprietary chip designs never leave your local Docker container.

---

## 🚀 How to Get Started

### 1. Purchase & Download
AgentIC is distributed as a highly optimized, standalone executable. You do not need to install Python or manage complex EDA toolchains natively.
1. Purchase a license at our official store: [👉 Get AgentIC](YOUR_LEMON_SQUEEZY_CHECKOUT_LINK)
2. You will receive an email containing:
   * Your secure download link for the executable (`agentic`).
   * Your unique **License Key** (starts with `sk_live_...`).

### 2. Installation
Place the downloaded executable into your desired working directory. 

*For Linux / macOS users, make it executable and move it to your global path:*
```bash
chmod +x agentic-linux
sudo mv agentic-linux /usr/local/bin/agentic
```

### 3. Authentication & Configuration
Before building chips, you must authenticate your machine using your purchased License Key. Our CLI will interactively help you link your LLM credentials securely.

Open your terminal and run:
```bash
agentic login sk_live_YOUR_LICENSE_KEY_HERE
```
*The wizard will ask you to securely input your NVIDIA, Groq, and/or GLM API keys to power the multi-agent reasoning engines.*

### 4. Build Your First Chip
Ensure **Docker** is running on your machine (required for OpenLane physical synthesis).

Run the engine from any folder:
```bash
agentic build --name fast_multiplier \
  --desc "A high-speed 16-bit pipelined hardware multiplier with an active-low synchronous reset." \
  --pdk-profile sky130 \
  --no-strict-gates
```

All generated Verilog, Testbenches, and GDS layouts will be strictly verified and saved into a local `designs/` directory.

---

## ⚖️ End User License Agreement (EULA)
By purchasing and downloading AgentIC, you agree to the terms of the License. Reverse engineering, redistribution, or unauthorized extraction of the internal architectures is strictly prohibited.

**[Contact Support]** | **[Report an Issue via GitHub Issues]**
