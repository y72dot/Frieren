#!/bin/bash
# Backup NapCat sessions + bot configs to backups/
set -e
cd "$(dirname "$0")/.."
D=backups/$(date +%Y%m%d_%H%M%S)
mkdir -p "$D"
cp -r instances/napcat-frieren/QQ "$D/napcat-frieren-session" 2>/dev/null || true
cp -r instances/frieren "$D/frieren-config" 2>/dev/null || true
ls -dt backups/*/ | tail -n +8 | xargs rm -rf 2>/dev/null || true
echo "Backup: $D"
