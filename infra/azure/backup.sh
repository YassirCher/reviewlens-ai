#!/usr/bin/env bash
set -euo pipefail
cd /srv/reviewlens/current
compose=(docker compose --env-file .env.production -f docker-compose.yml -f infra/azure/compose.production.yml)
backup_root=/srv/reviewlens/backups
install -d -m 0700 "$backup_root"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="$backup_root/$stamp"
install -d -m 0700 "$backup_dir"
restart_writers() {
    "${compose[@]}" up -d --no-deps --wait --wait-timeout 240 api worker scheduler >/dev/null
}
trap restart_writers EXIT
"${compose[@]}" stop --timeout 60 api worker scheduler >/dev/null
"${compose[@]}" exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$backup_dir/postgres.dump"
workspace_mount=$(docker volume inspect reviewlens_markdown_workspaces --format '{{.Mountpoint}}')
quarantine_mount=$(docker volume inspect reviewlens_markdown_quarantine --format '{{.Mountpoint}}')
tar -C "$workspace_mount" -czf "$backup_dir/workspaces.tar.gz" .
tar -C "$quarantine_mount" -czf "$backup_dir/quarantine.tar.gz" .
cp release.json "$backup_dir/release.json"
install -m 0600 .env.production "$backup_dir/production.env"
(cd "$backup_dir" && sha256sum postgres.dump production.env quarantine.tar.gz release.json workspaces.tar.gz >SHA256SUMS)
restart_writers
trap - EXIT
storage_account=$(python3 -c 'import json; print(json.load(open("release.json"))["backup_storage_account"])')
python3 infra/azure/upload_backup.py "$storage_account" "$backup_dir"
printf 'Backup completed: %s\n' "$stamp"
