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

const GID_RE = /^[a-z0-9_]+:[a-z_]+:[A-Za-z0-9_\/.:-]{1,160}$/;
const CATEGORY_RE = /^[a-z0-9_=:.-]{1,60}$/;

// Shared options: { size, focus: {lat, lon}, boundary: {minLon, minLat, maxLon, maxLat},
//                   circle: {lat, lon, radiusKm}, gid, country, categories: [...],
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
  if (opts.circle) {
    const c = opts.circle;
    params.set('boundary.circle.lat', finite(c.lat, 'circle.lat').toFixed(6));
    params.set('boundary.circle.lon', finite(c.lon, 'circle.lon').toFixed(6));
    params.set('boundary.circle.radius', String(Math.min(1000, Math.max(0.1, finite(c.radiusKm, 'radius')))));
  }
  if (opts.gid) {
    if (!GID_RE.test(opts.gid)) throw new PeliasError(`invalid boundary.gid: ${opts.gid}`);
    params.set('boundary.gid', opts.gid);
  }
  if (opts.country) {
    if (!/^[A-Za-z]{2,3}$/.test(opts.country)) throw new PeliasError('invalid boundary.country');
    params.set('boundary.country', opts.country);
  }
  const cats = cleanList(opts.categories, CATEGORY_RE, 'category');
  if (cats) params.set('categories', cats);
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
    this.lastMs = null; // duration of the last request, for display
  }

  async #get(path, params, signal) {
    const url = `${this.baseUrl}/v1/${path}?${params.toString()}`;
    const timeout = AbortSignal.timeout(this.timeoutMs);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let res;
    const t0 = performance.now();
    try {
      res = await fetch(url, { signal: combined, headers: { Accept: 'application/json' } });
      this.lastMs = Math.round(performance.now() - t0);
    } catch (err) {
      if (err.name === 'AbortError' && signal?.aborted) throw err; // caller cancelled
      if (err.name === 'TimeoutError' || timeout.aborted) {
        throw new PeliasError('request timed out', 0);
      }
      throw new PeliasError('network error', 0);
    }
    if (res.status === 429) throw new PeliasError('rate limited, slow down', 429);
    const type = res.headers.get('content-type') || '';
    if (!type.includes('json')) throw new PeliasError(`HTTP ${res.status}`, res.status);
    const body = await res.json();
    if (!res.ok && !body?.geocoding?.errors?.length) throw new PeliasError(`HTTP ${res.status}`, res.status);
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

  /**
   * Structured USPS address (pgeo extension /v1/address). Exactly one of:
   *   { ids: gid or [gids] } | { text } | { lat, lon }; optional unit ("Apt 2"), radiusKm.
   */
  address(q, signal) {
    const p = new URLSearchParams();
    if (q.ids) {
      const ids = [].concat(q.ids).map(String);
      for (const id of ids) if (!GID_RE.test(id)) throw new PeliasError(`invalid gid: ${id}`);
      p.set('ids', ids.join(','));
    } else if (q.text) {
      p.set('text', cleanText(q.text));
    } else {
      p.set('point.lat', finite(q.lat, 'lat').toFixed(6));
      p.set('point.lon', finite(q.lon, 'lon').toFixed(6));
    }
    const unit = String(q.unit ?? '').trim();
    if (unit) {
      if (!/^[A-Za-z0-9 #.-]{1,20}$/.test(unit)) throw new PeliasError('unit: up to 20 letters, digits, spaces, # . -');
      p.set('unit', unit);
    }
    if (q.radiusKm != null) p.set('radius', String(Math.min(5, Math.max(0.05, finite(q.radiusKm, 'radius')))));
    return this.#get('address', p, signal);
  }

  place(gids, signal) {
    const ids = [].concat(gids).map(String);
    for (const id of ids) {
      if (!GID_RE.test(id)) throw new PeliasError(`invalid gid: ${id}`);
    }
    return this.#get('place', new URLSearchParams({ ids: ids.join(',') }), signal);
  }
}
