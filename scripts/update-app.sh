#!/bin/bash

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <tag>"
    exit 1
fi

tag=$1
image="docker.io/xadamz23/reconcile:$tag"

cd "/home/dockerapps/reconcile"

running=$(docker ps --filter 'name=^reconcile$' --format '{{.Image}}')
if [[ "$running" == "$image" ]]; then
    echo "[INFO] app is already running the latest version ($tag)"
    exit 0
fi

if ! docker network inspect cf-tunnel >/dev/null 2>&1; then
    echo "[ERROR] docker network cf-tunnel does not exist; run: docker network create cf-tunnel"
    exit 1
fi

# The app's secrets (ANTHROPIC_API_KEY, for reading statements with Claude) live in
# .env beside the compose file, which loads them into the container. Without a key the
# app runs as before and just doesn't offer Read statement, but the file must exist.
if [[ ! -f .env ]]; then
    echo "[ERROR] /home/dockerapps/reconcile/.env is missing. Create it with ANTHROPIC_API_KEY=... and chmod 600 it (an empty file runs the app without statement reading)."
    exit 1
fi

echo "[INFO] stopping the container"
docker compose down

echo "[INFO] updating tag in compose file to $tag"

cat <<EOF > docker-compose.yaml
# Written by update-app.sh, which deploy.sh runs with the
# release tag. Don't edit by hand: the next deploy overwrites it.

name: reconcile

services:
  reconcile:
    image: $image
    platform: linux/amd64
    container_name: reconcile
    restart: unless-stopped
    environment:
      # Sets what "today" means (default transaction date, the Today divider).
      TZ: America/Chicago
    # ANTHROPIC_API_KEY, for reading statements with Claude. Not in this file: it's
    # rewritten on every deploy, and the key mustn't be in the repo's copy of this script.
    env_file: .env
    # No published ports: cloudflared reaches it at http://reconcile:8000 over cf-tunnel.
    networks:
      - cf-tunnel
    volumes:
      - reconcile-data:/data
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  reconcile-data:

networks:
  cf-tunnel:
    external: true
EOF

echo "[INFO] starting the container back up"
docker compose up -d
