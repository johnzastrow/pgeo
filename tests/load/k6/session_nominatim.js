// The same session as session.js, against Nominatim's API shape.
//
// Nominatim is the reference OpenStreetMap geocoder and the database Photon's index is exported
// from, so measuring it closes the OSM side of the comparison. The corpus, the query mix, the
// keystroke pattern, the think times, the ramp and the latency targets are identical to
// session.js: only the URL differs.
//
//   /search?q=<text>&format=geojson&addressdetails=1&limit=N       forward and type-ahead
//   /search?street=&city=&state=&postalcode=&format=geojson        structured (Nominatim has one)
//   /reverse?lat=&lon=&format=geojson&zoom=18                      reverse at house level
//
// Two notes on fairness. Nominatim has no autocomplete endpoint - that absence is why Photon
// exists - so the keystroke prefixes go to /search, which is what a client would have to do.
// addressdetails=1 is requested throughout so Nominatim does comparable work to engines that
// return a full address in every result.
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';

const BASE = (__ENV.BASE_URL || 'http://127.0.0.1:8081').replace(/\/+$/, '');
const VUS = Number(__ENV.VUS || 3);
const DURATION = __ENV.DURATION || '60s';
const WARMUP = __ENV.WARMUP || '15s';

const RAW = open('../corpus/maine_corpus.json');
const load = (key) => new SharedArray(key, () => JSON.parse(RAW)[key]);
const D = {
  addresses: load('addresses'), places: load('places'), venues: load('venues'),
  zips: load('zips'), structured: load('structured'), typos: load('typos'),
  variants: load('variants'), misses: load('misses'), foci: load('foci'),
  oa_points: load('oa_points'), random_points: load('random_points'),
  miss_points: load('miss_points'),
};

function seconds(d) {
  const m = /^(\d+)(ms|s|m)$/.exec(d);
  if (!m) return 60;
  return Number(m[1]) * (m[2] === 'ms' ? 0.001 : m[2] === 'm' ? 60 : 1);
}

export const options = {
  scenarios: {
    session: {
      executor: 'constant-vus', vus: VUS,
      duration: `${seconds(WARMUP) + seconds(DURATION)}s`,
      gracefulStop: '20s',
    },
  },
  thresholds: {
    // identical to the Pelias/pgeo/Photon sessions
    'http_req_duration{ep:autocomplete,phase:steady}': ['p(95)<=250'],
    'http_req_duration{ep:search,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:structured,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:reverse,phase:steady}': ['p(95)<=400'],
    // k6 only emits a sub-metric that a threshold names, and run_matrix.evaluate() reads the
    // steady-phase error rate and throughput from these two.
    'http_req_failed{phase:steady}': ['rate<0.01'],
    'http_reqs{phase:steady}': ['count>=0'],
    // Per query type (reporting only; SLOs are per endpoint).
    'http_req_duration{qtype:exact,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:typo,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:variant,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:miss,phase:steady}': ['max>=0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const think = () => sleep(3 + Math.random() * 5);

function phase() {
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  return elapsed < seconds(WARMUP) ? 'warmup' : 'steady';
}

function get(ep, path, params, qtype = 'exact') {
  const qs = Object.entries({ format: 'geojson', addressdetails: 1, ...params })
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const res = http.get(`${BASE}/${path}?${qs}`, {
    tags: { ep, qtype, phase: phase(), name: `/${path}` },
    timeout: '20s',
  });
  check(res, { 'status 200': (r) => r.status === 200 }, { ep });
  return res;
}

function textQuery(exactPool) {
  const q = Math.random();
  if (q < 0.65) return [pick(exactPool()), 'exact'];
  if (q < 0.77) return [pick(D.typos), 'typo'];
  if (q < 0.9) return [pick(D.variants), 'variant'];
  return [pick(D.misses), 'miss'];
}

function typeAhead() {
  const [text, qtype] = textQuery(() => {
    const r = Math.random();
    return r < 0.5 ? D.addresses : r < 0.75 ? D.places : D.venues;
  });
  for (let i = 2; i <= text.length; i += 1) {
    // No autocomplete endpoint: every keystroke is a full search, which is the point.
    get('autocomplete', 'search', { q: text.slice(0, i), limit: 8 }, qtype);
    sleep(0.2);
  }
  if (Math.random() < 0.5) get('search', 'search', { q: text, limit: 8 }, qtype);
}

function fullSearch() {
  const [text, qtype] = textQuery(() => {
    const r = Math.random();
    return r < 0.4 ? D.addresses : r < 0.65 ? D.places : r < 0.9 ? D.venues : D.zips;
  });
  get('search', 'search', { q: text, limit: 8 }, qtype);
}

function structured() {
  const s = pick(D.structured);
  const q = { limit: 8 };
  if (s.address) q.street = s.address;
  if (s.locality) q.city = s.locality;
  if (s.region) q.state = s.region;
  if (s.postalcode) q.postalcode = s.postalcode;
  get('structured', 'search', q);
}

function reverse() {
  const r = Math.random();
  const [lat, lon] = r < 0.6 ? pick(D.oa_points) : r < 0.9 ? pick(D.random_points) : pick(D.miss_points);
  // zoom=18 is house level, the closest equivalent to the other engines' layers=address.
  get('reverse', 'reverse', { lat, lon, zoom: 18 });
}

export default function () {
  const r = Math.random();
  if (r < 0.6) typeAhead();
  else if (r < 0.75) fullSearch();
  else if (r < 0.85) structured();
  else reverse();
  think();
}
