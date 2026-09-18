// Minimal, dependency-free client for the Pelias v1 API.
// Reusable outside this demo: import { PeliasClient } from './pelias-client.js'.
//
// All parameters are validated before they reach the URL; callers pass plain values,
// never pre-built query strings.

const LAYER_RE = /^[a-z_]{1,32}$/;
const SOURCE_RE = /^[a-z0-9_]{1,32}$/;
const MAX_TEXT = 200;

export class PeliasError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'PeliasError';
    this.status = status;
  }
}

function finite(n, name) {
  const v = Number(n);
  if (!Number.isFinite(v)) throw new PeliasError(`${name} must be a finite number`);
  return v;
}

function cleanText(text) {
  const t = String(text ?? '').trim();
  if (!t) throw new PeliasError('text is empty');
  return t.slice(0, MAX_TEXT);
}

function cleanList(values, re, name) {
  if (!values || values.length === 0) return null;
  for (const v of values) {
    if (!re.test(v)) throw new PeliasError(`invalid ${name}: ${v}`);
  }
  return values.join(',');
}

// Shared options: { size, focus: {lat, lon}, boundary: {minLon, minLat, maxLon, maxLat},
//                   layers: [...], sources: [...] }
function applyOptions(params, opts = {}) {
  if (opts.size != null) {
    params.set('size', String(Math.min(40, Math.max(1, Math.trunc(finite(opts.size, 'size'))))));
  }
  if (opts.focus) {
    params.set('focus.point.lat', finite(opts.focus.lat, 'focus.lat').toFixed(6));
    params.set('focus.point.lon', finite(opts.focus.lon, 'focus.lon').toFixed(6));
  }
  if (opts.boundary) {
    const b = opts.boundary;
    params.set('boundary.rect.min_lon', finite(b.minLon, 'minLon').toFixed(6));
    params.set('boundary.rect.min_lat', finite(b.minLat, 'minLat').toFixed(6));
    params.set('boundary.rect.max_lon', finite(b.maxLon, 'maxLon').toFixed(6));
    params.set('boundary.rect.max_lat', finite(b.maxLat, 'maxLat').toFixed(6));
  }
  const layers = cleanList(opts.layers, LAYER_RE, 'layer');
  if (layers) params.set('layers', layers);
  const sources = cleanList(opts.sources, SOURCE_RE, 'source');
  if (sources) params.set('sources', sources);
  return params;
}

export class PeliasClient {
  /**
   * @param {object} [config]
   * @param {string} [config.baseUrl]   API origin, '' for same origin (default)
   * @param {number} [config.timeoutMs] per-request timeout
   */
  constructor({ baseUrl = '', timeoutMs = 8000 } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.timeoutMs = timeoutMs;
  }

  async #get(path, params, signal) {
    const url = `${this.baseUrl}/v1/${path}?${params.toString()}`;
    const timeout = AbortSignal.timeout(this.timeoutMs);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let res;
    try {
      res = await fetch(url, { signal: combined, headers: { Accept: 'application/json' } });
    } catch (err) {
      if (err.name === 'AbortError' && signal?.aborted) throw err; // caller cancelled
      if (err.name === 'TimeoutError' || timeout.aborted) {
        throw new PeliasError('request timed out', 0);
      }
      throw new PeliasError('network error', 0);
    }
    if (res.status === 429) throw new PeliasError('rate limited, slow down', 429);
    const type = res.headers.get('content-type') || '';
    if (!res.ok || !type.includes('json')) {
      throw new PeliasError(`HTTP ${res.status}`, res.status);
    }
    const body = await res.json();
    if (body?.geocoding?.errors?.length) {
      throw new PeliasError(body.geocoding.errors.join('; '), res.status);
    }
    return body;
  }

  autocomplete(text, opts, signal) {
    const p = applyOptions(new URLSearchParams({ text: cleanText(text) }), opts);
    return this.#get('autocomplete', p, signal);
  }

  search(text, opts, signal) {
    const p = applyOptions(new URLSearchParams({ text: cleanText(text) }), opts);
    return this.#get('search', p, signal);
  }

  /** fields: { address, neighbourhood, locality, county, region, postalcode, country } */
  structured(fields, opts, signal) {
    const allowed = ['address', 'neighbourhood', 'borough', 'locality', 'county', 'region',
      'postalcode', 'country'];
    const p = new URLSearchParams();
    for (const k of allowed) {
      const v = String(fields?.[k] ?? '').trim();
      if (v) p.set(k, v.slice(0, MAX_TEXT));
    }
    if ([...p.keys()].length === 0) throw new PeliasError('at least one field is required');
    return this.#get('search/structured', applyOptions(p, opts), signal);
  }

  reverse(lat, lon, opts = {}, signal) {
    const p = new URLSearchParams({
      'point.lat': finite(lat, 'lat').toFixed(6),
      'point.lon': finite(lon, 'lon').toFixed(6),
    });
    if (opts.radiusKm != null) {
      p.set('boundary.circle.radius', String(Math.min(50, Math.max(0.01, finite(opts.radiusKm, 'radius')))));
    }
    return this.#get('reverse', applyOptions(p, { ...opts, focus: undefined, boundary: undefined }), signal);
  }

  place(gids, signal) {
    const ids = [].concat(gids).map(String);
    for (const id of ids) {
      if (!/^[a-z0-9_]+:[a-z_]+:[A-Za-z0-9_.:-]+$/.test(id)) throw new PeliasError(`invalid gid: ${id}`);
    }
    return this.#get('place', new URLSearchParams({ ids: ids.join(',') }), signal);
  }
}
