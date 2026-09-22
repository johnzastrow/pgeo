#!/usr/bin/env bash
# API keys for the edge (Phase 11).
#
#   scripts/edge_apikey.sh new <client-name>    make a key; print it ONCE and the inventory entry
#   scripts/edge_apikey.sh hash                 read a key on stdin, print its SHA-256 (for tests)
#
# A key is "pgeo_" plus 32 bytes from the OS CSPRNG as base64url (43 characters): recognisable
# to secret scanners, and with 256 bits of entropy there is nothing for a fast hash to expose,
# which is why the server stores plain SHA-256 rather than a password hash. The key is printed
# to the terminal and nowhere else: not to a file, not to the inventory, not to git. The entry
# that goes in group_vars/pelias/zz-local.yml (gitignored) is the hash and a name.
set -euo pipefail

case "${1:-}" in
  new)
    name="${2:-}"
    [[ "$name" =~ ^[a-z][a-z0-9-]{1,31}$ ]] || {
      echo "usage: $0 new <client-name>   (lowercase letters, digits, dashes; 2-32 chars)" >&2; exit 2; }
    key="pgeo_$(openssl rand 32 | basenc --base64url | tr -d '=\n')"
    [[ ${#key} -eq 48 ]] || { echo "unexpected key length ${#key}" >&2; exit 1; }
    hash="$(printf '%s' "$key" | sha256sum | cut -d' ' -f1)"
    cat <<MSG
API key for "$name" - shown once, store it where the client keeps secrets:

  $key

Add this to infra/ansible/group_vars/pelias/zz-local.yml under edge_api_keys, then
  cd infra/ansible && ansible-playbook site.yml --tags edge      (nginx reloads; no downtime)

  - name: $name
    sha256: $hash
MSG
    ;;
  hash)
    key="$(cat)"; key="${key%$'\n'}"
    printf '%s' "$key" | sha256sum | cut -d' ' -f1
    ;;
  *)
    sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
