#!/bin/bash
# Oracle Cloud Free Tier (Ampere ARM64) Environment Setup for Docker

echo "Setting up AgentIC Docker Environment for Oracle Cloud A1 (ARM64)..."

# 1. Update OS packages
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Docker and Docker Compose
echo "Ensuring Docker and Docker Compose are installed..."
if ! command -v docker &> /dev/null; then
    # Standard Docker installation via apt-get for Ubuntu 22.04
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    
    # Add current user to docker group
    sudo usermod -aG docker $USER
    echo "Docker installed! You may need to log out and back in, or run: newgrp docker"
else
    echo "Docker is already installed."
fi

# Ensure Docker Compose plugin is present
sudo apt-get install -y docker-compose-plugin docker-compose

# 3. Expose the API Port specifically for Oracle iptables
echo "Configuring Internal Firewall for Port 7860..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 7860 -j ACCEPT 2>/dev/null || true
if command -v netfilter-persistent &> /dev/null; then
    sudo netfilter-persistent save 2>/dev/null || true
else
    sudo apt-get install -y iptables-persistent
    sudo netfilter-persistent save 2>/dev/null || true
fi

# 4. Configure 8GB swap file
# Required for OpenLane GDSII synthesis builds on Oracle A1 (24GB RAM).
# Without swap, large synthesis runs can be OOM-killed mid-flight.
echo "Configuring 8GB swap file for OpenLane synthesis..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 8G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap configured (8GB)."
else
    echo "Swap file already exists, skipping."
fi

echo ""
echo "============================================================"
echo "Oracle A1 Setup Complete!"
echo ""
echo "Next steps:"
echo "  1. Copy .env.example to .env and fill in your LLM key"
echo "  2. Open port 7860 in Oracle Cloud Console:"
echo "     Networking -> VCN -> Security Lists -> Add Ingress Rule"
echo "  3. Run: docker compose up -d --build"
echo "============================================================"
