#!/bin/bash
# WillCraft AI - Deployment Script for AWS VPS
# Usage: ssh into your server, clone the repo, then run this script.

set -e

echo "=== WillCraft AI Deployment ==="

# 1. Check Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. Please log out and back in, then re-run this script."
    exit 1
fi

if ! command -v docker compose &> /dev/null; then
    echo "ERROR: docker compose not found. Install Docker Compose v2."
    exit 1
fi

# 2. Check .env file exists
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cat > .env << 'ENVEOF'
ANTHROPIC_API_KEY=your-anthropic-api-key-here
FLASK_SECRET_KEY=change-this-to-a-random-string
ENVEOF
    echo ""
    echo "IMPORTANT: Edit .env and set your ANTHROPIC_API_KEY and FLASK_SECRET_KEY"
    echo "  nano .env"
    echo ""
    echo "Then re-run this script."
    exit 1
fi

# Verify API key is set
source .env
if [ "$ANTHROPIC_API_KEY" = "your-anthropic-api-key-here" ]; then
    echo "ERROR: Please set ANTHROPIC_API_KEY in .env first."
    exit 1
fi

# 3. Create certs directory (for optional Cloudflare Origin Cert)
mkdir -p nginx/certs

# 4. Build and start
echo "Building and starting containers..."
docker compose up -d --build

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "App is running at http://$(curl -s ifconfig.me):80"
echo ""
echo "Next steps:"
echo "  1. In Cloudflare DNS, add an A record:"
echo "     Name: will    Value: $(curl -s ifconfig.me)    Proxy: ON (orange cloud)"
echo ""
echo "  2. In Cloudflare SSL/TLS, set mode to 'Flexible' (or 'Full' with Origin Cert)"
echo ""
echo "  3. Test: https://will.lifa.com.my"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f        # View logs"
echo "  docker compose restart         # Restart"
echo "  docker compose down            # Stop"
echo "  docker compose up -d --build   # Rebuild & restart"
