#!/bin/bash
# Backup NapCat sessions + bot configs to backups/
set -e
cd "$(dirname "$0")/.."
D=backups/$(date +%Y%m%d_%H%M%S)
mkdir -p "$D"
for d in instances/napcat-*/; do
    name=$(basename "$d")
    cp -r "$d/QQ" "$D/$name-session" 2>/dev/null || true
done
for d in instances/*/; do
    [[ "$d" == instances/napcat-* ]] && continue
    name=$(basename "$d")
    cp -r "$d" "$D/$name-config" 2>/dev/null || true
done
ls -dt backups/*/ | tail -n +8 | xargs rm -rf 2>/dev/null || true
echo "Backup: $D"
