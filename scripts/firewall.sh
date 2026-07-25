#!/bin/bash
set -e

echo "=== Firewall Setup ==="

# Install ufw if not present
if ! command -v ufw &> /dev/null; then
    apt update && apt install -y ufw
fi

# Reset to defaults
ufw --force reset

# Default policies
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (important — don't lock yourself out)
ufw allow 22/tcp comment "SSH"

# Allow HTTP and HTTPS
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"

# Enable
ufw --force enable

echo ""
echo "✅ Firewall configured. Open ports: 22 (SSH), 80 (HTTP), 443 (HTTPS)"
ufw status verbose
