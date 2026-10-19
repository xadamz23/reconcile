#!/bin/bash

tag=$1

cd "/home/dockerapps/reconcile"

docker ps | grep reconcile | grep -q "$tag"
if [[ $? -eq 0 ]]; then
    echo "[INFO] app is already running the latest version ($tag)"
    exit 0
fi

echo "[INFO] stopping the container"
docker compose down

echo "[INFO] updating tag in compose file to $tag"

cat <<EOF > docker-compose.yaml
# Production compose file. The image is built and pushed from the dev machine
# (scripts/release.sh); the server only needs this file and a .env next to it:
#
#   TZ=America/Chicago
#   RECONCILE_TAG=v0.1.0      # a git tag; defaults to latest
#   RECONCILE_PORT=8000       # optional
#
# Deploy or update:
#   docker compose pull && docker compose up -d
# Roll back: set RECONCILE_TAG to an older tag and run the same command.

services:
  reconcile:
    image: docker.io/xadamz23/reconcile:$tag
    platform: linux/amd64
    container_name: reconcile
    restart: unless-stopped
    environment:
      # Sets what "today" means (default transaction date, the Today divider).
      TZ: America/Chicago
    ports:
      # Bound to localhost only: Cloudflare (cloudflared on this host) is the way in.
      # If cloudflared runs in another container, drop this and put both on a shared network.
      - "0.0.0.0:8004:8000"
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
  default:
    external: true
    name: proxied_to_localhost
EOF

echo "[INFO] starting the container back up"
docker compose up -d
