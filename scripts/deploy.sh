#!/bin/bash
# Ubuntu cloud server — Docker Compose deployment
set -e
cd "$(dirname "$0")/.."

echo "=== Building qqbot image ==="
docker compose build

echo ""
echo "=== Copy .env template if needed ==="
for inst in instances/*/; do
    [ ! -f "${inst}.env" ] && cp .env.example "${inst}.env" && echo "${inst}.env created from template" || echo "${inst}.env already exists"
done

echo ""
echo "=== Starting NapCat containers ==="
docker compose up -d $(docker compose config --services | grep napcat)

echo ""
echo "=== Done (initial setup) ==="
echo "NapCat WebUI: ssh -L 6099:127.0.0.1:6099 user@host, then open http://localhost:6099"
echo "Check logs:  docker compose logs -f"
echo "After QR login, start bots: docker compose up -d"
