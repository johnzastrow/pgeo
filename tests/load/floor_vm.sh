#!/usr/bin/env bash
# Confirm a minimum server on a real VM: create a temporary Proxmox VM of the given size, deploy one
# engine (plus the nginx edge), run the 3-user validation and the full ramp through the edge, then
# destroy the VM. Approved 2026-09-19 (temporary VMs 190-199, destroyed after use).
#
#   tests/load/floor_vm.sh <pgeo|pelias> <vmid 190-199> <cores> <cpulimit> <memory_mb> [ansible -e args...]
#
# Example (pgeo, half a core, 1.5 GB):
#   tests/load/floor_vm.sh pgeo 190 1 0.5 1536 -e pgeo_dump_name=pgeo-0.7.0-... \
#       -e '{"pgeo_pg": {"shared_buffers": "96MB", "effective_cache_size": "288MB", "work_mem": "4MB"}}'
#
# The engine is reached through an SSH tunnel to the VM's edge (nginx), so requests take the same
# path as on VM 120 minus TLS. Results: data/loadtest/<time>-vm/ (tests/load/run_vm.py).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# The Proxmox host. Set PVE_HOST for your own: export PVE_HOST=root@10.0.0.10
PVE="${PVE_HOST:-root@192.0.2.10}"

engine="${1:?engine}"; vmid="${2:?vmid}"; cores="${3:?cores}"; cpulimit="${4:?cpulimit}"; mem="${5:?memory_mb}"
shift 5
extra=("$@")
name="floor-${engine}"

# Guard rails: only temporary ids, never the production VM
[[ "$engine" == pgeo || "$engine" == pelias ]] || { echo "engine must be pgeo or pelias" >&2; exit 2; }
[[ "$vmid" =~ ^19[0-9]$ ]] || { echo "vmid must be 190-199 (temporary range)" >&2; exit 2; }
[[ "$cores" =~ ^[1-8]$ && "$mem" =~ ^[0-9]{3,5}$ && "$cpulimit" =~ ^[0-9.]+$ ]] || { echo "bad size" >&2; exit 2; }

pubkey="$(ssh-add -L | grep -m1 '^ssh-ed25519 ')"
cleanup() {
  [[ -n "${tunnel_pid:-}" ]] && kill "$tunnel_pid" 2>/dev/null || true
  echo "== destroying VM $vmid ($name)"
  ssh -o BatchMode=yes "$PVE" "qm status $vmid >/dev/null 2>&1 && qm config $vmid | grep -q '^name: $name\$' \
    && (qm stop $vmid --skiplock 1 || true) && qm destroy $vmid --purge 1 && rm -f /var/lib/vz/snippets/$name-vendor.yaml" || true
}
trap cleanup EXIT

echo "== creating VM $vmid: $cores vCPU (limit $cpulimit), $mem MB"
disk=40G; [[ "$engine" == pelias ]] && disk=60G
out="$(ssh -o BatchMode=yes "$PVE" "VMID=$vmid NAME=$name CORES=$cores MEMORY_MB=$mem DISK_SIZE=$disk SSH_PUBKEY='$pubkey' bash -s" \
  < "$ROOT/infra/proxmox/create_vm.sh")"
echo "$out"
ip="$(grep -oE 'is up at [0-9.]+' <<<"$out" | awk '{print $4}')"
[[ -n "$ip" ]] || { echo "no IP reported" >&2; exit 1; }
if [[ "$cpulimit" != "0" && "$cpulimit" != "$cores" ]]; then
  ssh -o BatchMode=yes "$PVE" "qm set $vmid --cpulimit $cpulimit"
fi
ssh -o BatchMode=yes "$PVE" "qm set $vmid --onboot 0 --tags 'floor;temporary'"

echo "== waiting for SSH on $ip"
for _ in $(seq 1 60); do
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "jcz@$ip" true 2>/dev/null && break
  sleep 5
done

inv="$(mktemp --suffix=.yml)"   # Ansible only parses a YAML inventory if the file ends .yml
cat > "$inv" <<EOF
all:
  children:
    pelias:
      hosts:
        $name:
          ansible_host: $ip
          ansible_user: jcz
          ansible_ssh_common_args: "-o StrictHostKeyChecking=accept-new"
          exposure: lan
EOF

echo "== deploying $engine"
roles="base,docker,edge"
# JSON, not key=value: Ansible reads key=value as a plain string, and the template then
# iterates its characters and writes "allow [;" into the nginx config.
vars=(-e "{\"edge_allowed_sources\": [\"192.0.2.254\", \"$ip\"], \"edge_rate_search\": 2000, \"edge_rate_autocomplete\": 5000}")
if [[ "$engine" == pgeo ]]; then
  roles="$roles,pgeo"; vars+=(-e pgeo_enabled=true)
else
  roles="$roles,pelias"; vars+=(-e pgeo_enabled=false -e pelias_snapshot_name=pelias-20260918-1715)
fi
(cd "$ROOT/infra/ansible" && ansible-playbook -i "$inv" site.yml --tags "$roles" "${vars[@]}" "${extra[@]}")
# An inventory Ansible cannot parse matches no hosts, prints an empty PLAY RECAP and exits 0,
# which then looks like a deployed engine that answers nothing. Check the host is really there.
ansible -i "$inv" pelias --list-hosts 2>/dev/null | grep -q "$name" \
  || { echo "inventory matched no hosts: nothing was deployed" >&2; exit 1; }
ssh -o BatchMode=yes "jcz@$ip" "sudo -n docker ps --format '{{.Names}}'" 2>/dev/null | grep -q pgeo_db \
  || [[ "$engine" == pelias ]] \
  || { echo "pgeo containers are not running on $ip: deployment did not take" >&2; exit 1; }

echo "== tunnel and load test"
ssh -o BatchMode=yes -N -L 18080:"$ip":8080 "jcz@$ip" &
tunnel_pid=$!
sleep 3
python3 "$ROOT/tests/load/run_vm.py" --engines "$engine" --base http://127.0.0.1:18080 --ssh "jcz@$ip" \
  --label "floor-${cores}c-${cpulimit}-${mem}mb" --cpus "$cores" --cpulimit "$cpulimit" \
  --memory-gb "$(python3 -c "print(round($mem / 1024, 2))")" --host "temporary VM $vmid ($name)"
