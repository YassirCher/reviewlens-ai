#!/usr/bin/env bash
set -euo pipefail
release_dir=$(realpath "${1:?Pass the uploaded release directory}")
case "$release_dir" in /srv/reviewlens/releases/*) ;; *) echo 'Unexpected release directory.' >&2; exit 1 ;; esac
cd "$release_dir"
expected_sha=$(python3 -c 'import json; print(json.load(open("release.json"))["archive_sha256"])')
printf '%s  release.tar\n' "$expected_sha" | sha256sum -c -
tar -xf release.tar
chmod 0600 .env.production
revision=$(python3 -c 'import json; print(json.load(open("release.json"))["revision"])')
hostname=$(python3 -c 'import json; print(json.load(open("release.json"))["hostname"])')
backend_image=$(python3 -c 'import json; print(json.load(open("release.json"))["backend_image"])')
frontend_image=$(python3 -c 'import json; print(json.load(open("release.json"))["frontend_image"])')
docker build --target runtime --label "org.opencontainers.image.revision=$revision" -t "$backend_image" backend
docker build --build-arg "NEXT_PUBLIC_API_BASE_URL=https://$hostname" --label "org.opencontainers.image.revision=$revision" -t "$frontend_image" frontend
compose=(docker compose --env-file .env.production -f docker-compose.yml -f infra/azure/compose.production.yml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait --wait-timeout 240 postgres redis neo4j qdrant
"${compose[@]}" up migrate --no-deps --abort-on-container-exit --exit-code-from migrate
"${compose[@]}" up -d --wait --wait-timeout 360 api worker scheduler frontend proxy
ln -sfn "$release_dir" /srv/reviewlens/current
docker image inspect "$backend_image" "$frontend_image" --format '{{.RepoTags}} {{.Id}}'
"${compose[@]}" ps
