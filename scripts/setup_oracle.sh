#!/bin/bash
# Oracle Cloud Free Tier (Ampere ARM64) Environment Setup for Docker

echo "🚀 Setting up AgentIC Docker Environment for Oracle Cloud A1 (ARM64)..."

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
    echo "⚠️ Docker installed! You may need to log out and log back in or run 'newgrp docker' to use it without sudo."
else
    echo "✅ Docker is already installed."
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

echo "✅ Setup Complete. Ensure your .env is configured, then run: docker-compose up -d --build"
