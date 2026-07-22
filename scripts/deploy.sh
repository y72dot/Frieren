#!/bin/bash
# Ubuntu cloud server — Docker Compose deployment
set -e
cd "$(dirname "$0")/.."

echo "=== Building qqbot image ==="
docker compose build

echo ""
echo "=== Copy .env template if needed ==="
ENV_FILE="instances/frieren/.env"
[ ! -f "$ENV_FILE" ] && cp .env.example "$ENV_FILE" && echo "$ENV_FILE created from template" || echo "$ENV_FILE already exists"

echo ""
echo "=== Starting NapCat container ==="
docker compose up -d napcat-frieren

echo ""
echo "=== Done (initial setup) ==="
echo "NapCat WebUI: ssh -L 6099:127.0.0.1:6099 user@host, then open http://localhost:6099"
echo "Check logs:  docker compose logs -f"
echo "After QR login, start bot: docker compose up -d qqbot-frieren"
