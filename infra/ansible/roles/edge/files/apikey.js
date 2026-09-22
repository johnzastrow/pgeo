// API-key check for the edge (Phase 11). Runs inside nginx via the njs module.
//
// The client presents a key in the X-API-Key header. This returns its SHA-256 as hex; nginx maps
// that hash to a client name (edge_api_keys in the Ansible inventory) and refuses the request
// when the map finds nothing. So the server holds hashes only, and a key never appears in a
// config file, an access log or a URL.
//
// SHA-256 rather than a password hash on purpose: keys are 32 random bytes from a CSPRNG
// (scripts/edge_apikey.sh), so there is no low-entropy secret for a fast hash to expose - the
// slow hashes exist for passwords people choose. Comparison is a map lookup on the hash, which
// leaks nothing useful about the key: learning that two hashes share a prefix does not help
// find a preimage.
function keyHash(r) {
  const key = r.headersIn['X-API-Key'];
  // Keys are pgeo_ plus 43 base64url characters. Anything else is not a key; return a value no
  // real hash can equal so the map falls through to "" without hashing attacker-sized input.
  if (!key || key.length !== 48 || !/^pgeo_[A-Za-z0-9_-]{43}$/.test(key)) {
    return 'none';
  }
  return require('crypto').createHash('sha256').update(key).digest('hex');
}

export default { keyHash };
