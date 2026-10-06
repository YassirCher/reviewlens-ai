#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

data_disk=/dev/disk/azure/scsi1/lun0
if [[ ! -b "$data_disk" ]]; then
    echo 'Expected ReviewLens data disk is unavailable.' >&2
    exit 1
fi
root_device=$(findmnt -n -o SOURCE /)
root_parent=$(lsblk -n -o PKNAME "$root_device")
if [[ "$(readlink -f "$data_disk")" == "$(readlink -f "$root_device")" ]] || [[ -n "$root_parent" && "$(readlink -f "$data_disk")" == "/dev/$root_parent" ]]; then
    echo 'Refusing to format the operating-system device.' >&2
    exit 1
fi
if ! blkid "$data_disk" >/dev/null 2>&1; then
    mkfs.ext4 -q "$data_disk"
fi
data_uuid=$(blkid -s UUID -o value "$data_disk")
install -d -m 0750 /srv
if ! grep -q "UUID=$data_uuid " /etc/fstab; then
    printf 'UUID=%s /srv ext4 defaults,nofail 0 2\n' "$data_uuid" >>/etc/fstab
fi
mountpoint -q /srv || mount /srv
systemctl stop docker.service docker.socket
install -d -m 0710 /srv/docker
printf '{"data-root":"/srv/docker"}\n' >/etc/docker/daemon.json
systemctl enable --now docker
install -d -m 0750 -o reviewlens -g reviewlens /srv/reviewlens
touch /srv/reviewlens/.bootstrap-ready
