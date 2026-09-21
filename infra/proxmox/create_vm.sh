#!/usr/bin/env bash
# Clone the Debian 13 template into the Pelias query VM and start it.
# Runs ON the Proxmox host. The SSH public key is passed in as SSH_PUBKEY (content, not path):
#   ssh root@YOUR-PROXMOX-HOST "SSH_PUBKEY='$(cat ~/.ssh/id_ed25519.pub)' bash -s" < infra/proxmox/create_vm.sh
set -euo pipefail

TEMPLATE_ID="${TEMPLATE_ID:-9013}"
VMID="${VMID:-120}"
NAME="${NAME:-pelias-maine}"
STORAGE="${STORAGE:-nvme2tb}"
CORES="${CORES:-4}"
MEMORY_MB="${MEMORY_MB:-10240}"
DISK_SIZE="${DISK_SIZE:-120G}"
CI_USER="${CI_USER:-jcz}"
SNIPPET_STORAGE="${SNIPPET_STORAGE:-local}"
: "${SSH_PUBKEY:?set SSH_PUBKEY to the public key content}"

if qm status "$VMID" >/dev/null 2>&1; then
  echo "VMID ${VMID} already exists; refusing to overwrite" >&2
  exit 1
fi
case "$SSH_PUBKEY" in
  ssh-ed25519\ *|ecdsa-sha2-*|ssh-rsa\ *) ;;
  *) echo "SSH_PUBKEY does not look like a public key" >&2; exit 1 ;;
esac

# Vendor cloud-init snippet: install the guest agent (so Proxmox can report the DHCP IP),
# enable it, and nothing else. Everything else is Ansible's job.
snippet_dir="$(pvesm path "${SNIPPET_STORAGE}:snippets/x" 2>/dev/null | xargs dirname)" || true
if [[ -z "$snippet_dir" ]]; then
  echo "storage ${SNIPPET_STORAGE} has no 'snippets' content type; enable it:" >&2
  echo "  pvesm set ${SNIPPET_STORAGE} --content <existing>,snippets" >&2
  exit 1
fi
mkdir -p "$snippet_dir"
cat > "${snippet_dir}/${NAME}-vendor.yaml" <<'YAML'
#cloud-config
package_update: true
packages: [qemu-guest-agent]
runcmd:
  - systemctl enable --now qemu-guest-agent
YAML

pubkey_file="$(mktemp)"
trap 'rm -f "$pubkey_file"' EXIT
printf '%s\n' "$SSH_PUBKEY" > "$pubkey_file"

qm clone "$TEMPLATE_ID" "$VMID" --name "$NAME" --full 1 --storage "$STORAGE"
qm set "$VMID" --cores "$CORES" --memory "$MEMORY_MB" --balloon 0 \
  --ciuser "$CI_USER" --sshkeys "$pubkey_file" --ipconfig0 ip=dhcp \
  --cicustom "vendor=${SNIPPET_STORAGE}:snippets/${NAME}-vendor.yaml" \
  --onboot 1 --tags "pelias;geocoder"
qm resize "$VMID" scsi0 "$DISK_SIZE"
qm start "$VMID"

# Wait for the guest agent to report an IPv4 address on the LAN.
for _ in $(seq 1 60); do
  ip="$(qm guest cmd "$VMID" network-get-interfaces 2>/dev/null \
        | grep -oE '"ip-address" *: *"192\.168\.[0-9]+\.[0-9]+"' | head -1 \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || true)"   # not [0-9.]+$: the line ends with a quote
  if [[ -n "$ip" ]]; then echo "VM ${VMID} (${NAME}) is up at ${ip}"; exit 0; fi
  sleep 5
done
echo "VM ${VMID} started but no IP reported yet; check: qm guest cmd ${VMID} network-get-interfaces" >&2
