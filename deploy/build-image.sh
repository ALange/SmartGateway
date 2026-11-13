#!/usr/bin/env bash
# Build the image for the TARGET host and package it with the deploy files.
#   ./deploy/build-image.sh [linux/amd64|linux/arm64]     (default: linux/amd64)
# Result: dist-deploy/  (copy this folder to the other host)
set -euo pipefail
PLATFORM="${1:-linux/amd64}"
cd "$(dirname "$0")/.."
OUT=dist-deploy; rm -rf "$OUT"; mkdir -p "$OUT"
docker buildx build --platform "$PLATFORM" --load -t smartgateway:latest .
docker save smartgateway:latest | gzip > "$OUT/smartgateway-image.tar.gz"
cp deploy/docker-compose.yml deploy/.env.example "$OUT/"
echo "Done: $OUT/  ($(du -h "$OUT/smartgateway-image.tar.gz" | cut -f1) image)"
echo "On the target:  gunzip -c smartgateway-image.tar.gz | docker load && cp .env.example .env && docker compose up -d"
