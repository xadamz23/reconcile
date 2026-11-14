#!/bin/bash
#
# Usage: ./deploy.sh [--minor | --major | X.Y.Z]
#
# With no argument, bumps the patch number of the newest X.Y.Z tag on origin
# (read with ls-remote, so local tags don't need to be fetched).

set -euo pipefail

usage() {
    echo "Usage: $0 [--minor | --major | X.Y.Z]   (default: patch bump of newest remote tag)"
    exit 1
}

semver='^[0-9]+\.[0-9]+\.[0-9]+$'

bump=patch
explicit=""
if [[ $# -gt 1 ]]; then
    usage
elif [[ $# -eq 1 ]]; then
    case "$1" in
        --minor) bump=minor ;;
        --major) bump=major ;;
        -h|--help) usage ;;
        *)
            [[ "$1" =~ $semver ]] || usage
            explicit=$1
            ;;
    esac
fi

# newest release tag on the remote
if ! remote_tags=$(git ls-remote --tags --refs origin); then
    echo "[ERROR] couldn't list tags on origin"
    exit 1
fi
latest=$(awk -F/ '{print $3}' <<<"$remote_tags" | grep -E "$semver" | sort -V | tail -1 || true)
if [[ -z "$latest" ]]; then
    echo "[ERROR] no X.Y.Z tags found on origin; pass a tag explicitly"
    [[ -n "$explicit" ]] || exit 1
fi

if [[ -n "$explicit" ]]; then
    tag=$explicit
    if [[ -n "$latest" ]]; then
        if awk -F/ '{print $3}' <<<"$remote_tags" | grep -qxF "$tag"; then
            echo "[ERROR] tag $tag already exists on origin"
            exit 1
        fi
        if [[ "$(printf '%s\n%s\n' "$latest" "$tag" | sort -V | tail -1)" != "$tag" ]]; then
            echo "[ERROR] $tag is not newer than the latest tag ($latest)"
            exit 1
        fi
    fi
else
    IFS=. read -r major minor patch <<<"$latest"
    case "$bump" in
        patch) patch=$((patch + 1)) ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        major) major=$((major + 1)); minor=0; patch=0 ;;
    esac
    tag="$major.$minor.$patch"
fi

commit=$(git rev-parse --short HEAD)
branch=$(git rev-parse --abbrev-ref HEAD)
echo "[INFO] latest ${latest:-none} -> $tag (commit $commit on $branch)"
if [[ $# -eq 0 ]]; then
    IFS=. read -r x y _ <<<"$latest"
    echo "[INFO] for a bigger bump: $0 --minor ($x.$((y + 1)).0) or $0 --major ($((x + 1)).0.0), or pass X.Y.Z"
fi
read -r -p "Deploy $tag? [y/N] " answer
if [[ "$answer" != [yY] ]]; then
    echo "[INFO] aborted"
    exit 1
fi

# tag release
echo "[INFO] tagging release ($tag)"
git tag -a "$tag" -m "Release version $tag"
git push origin "$tag"

# build & push docker image
echo "[INFO] building docker image"
docker buildx build --platform linux/amd64 -t "docker.io/xadamz23/reconcile:$tag" --load .

echo "[INFO] pushing docker image"
docker push "xadamz23/reconcile:$tag"

# ssh to host and update app
echo "[INFO] ssh'ing to docker-public vm"
ssh -o BatchMode=yes root@docker-public.stizzle.org "/home/dockerapps/reconcile/update-app.sh $tag"
