#!/usr/bin/env bash
# Set up the Ansible inventory for a server that will serve a pgeo build, and issue its first
# API key. Asks for the handful of values that are specific to your network, writes the two
# gitignored files, and prints the command that deploys.
#
#   scripts/new_host.sh [--build ny]
#
# It writes:
#   infra/ansible/inventory/hosts.yml              the server's address and login
#   infra/ansible/group_vars/pelias/zz-local.yml   everything specific to your network
#
# Neither is committed. An existing file is never overwritten without being asked.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

build=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ask() {  # ask VAR "prompt" ["default"]
  local __var="$1" prompt="$2" default="${3:-}" reply
  if [[ -n "$default" ]]; then
    read -r -p "  ${prompt} [${default}]: " reply
    reply="${reply:-$default}"
  else
    while [[ -z "${reply:-}" ]]; do read -r -p "  ${prompt}: " reply; done
  fi
  printf -v "$__var" '%s' "$reply"
}

confirm_overwrite() {  # never clobber a file that already holds someone's real values
  local f="$1" reply
  [[ -e "$f" ]] || return 0
  read -r -p "  $f exists. Overwrite? [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]] || { echo "  keeping $f"; return 1; }
}

[[ -t 0 ]] || { echo "scripts/new_host.sh asks questions; run it from a terminal" >&2; exit 1; }

echo "A server to serve one pgeo build. Answers go into two gitignored files."
echo
[[ -n "$build" ]] || ask build "Which build does it serve (a state code, or a name from regions/regions.json)" "me"
build="$(tr ',' '-' <<<"${build,,}")"
ask host_addr "The server's address (IP or DNS name)"
ask host_user "SSH user on the server" "$(id -un)"
ask admin_cidr "The network you administer from, as CIDR" "203.0.113.0/24"
ask tls_addr "The address of whatever terminates TLS in front of it"
ask server_name "The hostname people will use" "geocoder.example.org"
ask key_name "A name for the first API key (the client that will use it)" "demo"

hosts=infra/ansible/inventory/hosts.yml
local_vars=infra/ansible/group_vars/pelias/zz-local.yml

echo
if confirm_overwrite "$hosts"; then
  cat > "$hosts" <<EOF
# Written by scripts/new_host.sh. Not committed.
all:
  children:
    pelias:
      hosts:
        pgeo-${build}:
          ansible_host: ${host_addr}
          ansible_user: ${host_user}
          exposure: public
EOF
  echo "  wrote $hosts"
fi

# The key is issued first: its hash goes straight into the file, so there is no step where the
# inventory exists but refuses every client.
echo
key_out="$(scripts/edge_apikey.sh new "$key_name")"
printf '%s\n' "$key_out"
key_hash="$(printf '%s' "$key_out" | sed -n 's/.*sha256: *\([0-9a-f]\{64\}\).*/\1/p' | tail -1)"
[[ -n "$key_hash" ]] || { echo "could not read the key hash from edge_apikey.sh output" >&2; exit 1; }

if confirm_overwrite "$local_vars"; then
  cat > "$local_vars" <<EOF
# Written by scripts/new_host.sh. Not committed: it holds this network's real addresses.
# Ansible loads every file in this directory; the zz- prefix makes it sort after main.yml.

pelias_enabled: false            # pgeo only; it then serves the canonical /v1/* paths
pgeo_build: ${build}   # the basemap to ship, and what the demo page calls itself

ssh_allowed_sources: ["${admin_cidr}"]
edge_allowed_sources: ["${tls_addr}"]      # the TLS terminator, and only it
edge_trusted_proxies: ["${tls_addr}"]
edge_server_name: ${server_name}

# One entry per client. Add more with: scripts/edge_apikey.sh new <name>
edge_api_keys:
  - name: ${key_name}
    sha256: ${key_hash}
EOF
  chmod 600 "$local_vars"
  echo "  wrote $local_vars"
fi

dump_dir="data/pgeo$([[ "$build" == me ]] || echo "-${build}")/dumps"
latest="$(ls -t "${dump_dir}"/*.dump 2>/dev/null | head -1 || true)"
echo
echo "Next:"
if [[ -n "$latest" ]]; then
  echo "  cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=$(basename "${latest%.dump}")"
else
  echo "  build one first:  scripts/build_region.sh --build ${build}"
  echo "  then:             cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=<name>"
fi
echo "  and point your TLS terminator at ${host_addr}:8080 for ${server_name}"
