#!/bin/bash
# Ubuntu cloud server deployment script
set -e

cd "$(dirname "$0")/.."

echo "=== Installing Python dependencies ==="
python3 -m venv venv
source venv/bin/activate
pip install -e .

echo ""
echo "=== Copy config template if needed ==="
[ ! -f .env ] && cp .env.example .env && echo ".env created from template" || echo ".env already exists"

echo ""
echo "=== Start with PM2 ==="
pm2 start ecosystem.config.json
pm2 save
pm2 startup

echo ""
echo "=== Done ==="
echo "Bot logs: pm2 logs qqbot"
echo "NapCat WebUI: http://<server-ip>:6099/webui"
