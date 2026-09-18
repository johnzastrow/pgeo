#!/usr/bin/env bash
# Create a Debian 13 cloud-init VM template on a Proxmox node.
# Runs ON the Proxmox host, e.g.:
#   ssh root@192.0.2.10 'bash -s' < infra/proxmox/create_template.sh
# Override defaults with env vars on the remote side:
#   ssh root@HOST 'TEMPLATE_ID=9013 STORAGE=nvme2tb bash -s' < create_template.sh
set -euo pipefail

TEMPLATE_ID="${TEMPLATE_ID:-9013}"
TEMPLATE_NAME="${TEMPLATE_NAME:-debian13-cloud}"
STORAGE="${STORAGE:-nvme2tb}"
BRIDGE="${BRIDGE:-vmbr0}"
IMAGE_URL="${IMAGE_URL:-https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2}"
SUMS_URL="${SUMS_URL:-https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS}"
WORK="${WORK:-/var/lib/vz/template/cache}"

if qm status "$TEMPLATE_ID" >/dev/null 2>&1; then
  echo "VMID ${TEMPLATE_ID} already exists; refusing to overwrite" >&2
  exit 1
fi
pvesm status --storage "$STORAGE" >/dev/null || { echo "storage ${STORAGE} not found" >&2; exit 1; }

# Always fetch a fresh image and verify it against Debian's published SHA512SUMS.
mkdir -p "$WORK"
img="${WORK}/$(basename "$IMAGE_URL")"
curl -fsSL -o "${img}.part" "$IMAGE_URL"
curl -fsSL -o "${WORK}/SHA512SUMS" "$SUMS_URL"
expected="$(awk -v f="$(basename "$IMAGE_URL")" '$2==f {print $1}' "${WORK}/SHA512SUMS")"
actual="$(sha512sum "${img}.part" | awk '{print $1}')"
[[ -n "$expected" && "$expected" == "$actual" ]] || { echo "checksum mismatch for ${img}" >&2; exit 1; }
mv "${img}.part" "$img"

qm create "$TEMPLATE_ID" --name "$TEMPLATE_NAME" --ostype l26 \
  --machine q35 --bios ovmf --cpu host --cores 2 --memory 2048 --balloon 0 \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --scsihw virtio-scsi-single --agent enabled=1,fstrim_cloned_disks=1 \
  --serial0 socket --vga serial0
qm set "$TEMPLATE_ID" --efidisk0 "${STORAGE}:0,efitype=4m,pre-enrolled-keys=0"
qm set "$TEMPLATE_ID" --scsi0 "${STORAGE}:0,import-from=${img},discard=on,iothread=1,ssd=1"
qm set "$TEMPLATE_ID" --ide2 "${STORAGE}:cloudinit" --boot order=scsi0
qm template "$TEMPLATE_ID"
echo "template ${TEMPLATE_ID} (${TEMPLATE_NAME}) created on ${STORAGE}"
