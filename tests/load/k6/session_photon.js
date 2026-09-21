// The same session as session.js, against Photon's API shape.
//
// Photon is a type-ahead specialist, so the comparison that matters is autocomplete. The corpus,
// the query mix, the keystroke pattern, the think times, the ramp and the latency targets are
// identical to session.js: only the URL differs, because Photon does not speak the Pelias API.
//
//   /api?q=<text>&limit=N[&lat=&lon=]     forward and type-ahead
//   /reverse?lat=&lon=[&limit=N]          reverse
//
// Photon has no structured-search endpoint without a Nominatim-backed import option, so the
// structured share of the session is sent as a single-line query instead, and tagged as such.
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';

const BASE = (__ENV.BASE_URL || 'http://127.0.0.1:2322').replace(/\/+$/, '');
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
    // identical to the Pelias/pgeo session
    'http_req_duration{ep:autocomplete,phase:steady}': ['p(95)<=250'],
    'http_req_duration{ep:search,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:structured,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:reverse,phase:steady}': ['p(95)<=400'],
    // k6 only emits a sub-metric that a threshold names, and run_matrix.evaluate() reads the
    // steady-phase error rate and throughput from these two. Without them both read as 0.0 -
    // silently, and the error rate would read clean however the engine behaved.
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
  const qs = Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const res = http.get(`${BASE}/${path}?${qs}`, {
    tags: { ep, qtype, phase: phase(), name: `/${path}` },
    timeout: '20s',
  });
  check(res, { 'status 200': (r) => r.status === 200 }, { ep });
  return res;
}

function focus() {
  const [lat, lon] = pick(D.foci);
  return { lat, lon };
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
  const f = focus();
  for (let i = 2; i <= text.length; i += 1) {
    get('autocomplete', 'api', { q: text.slice(0, i), limit: 8, ...f }, qtype);
    sleep(0.2);
  }
  if (Math.random() < 0.5) get('search', 'api', { q: text, limit: 8, ...f }, qtype);
}

function fullSearch() {
  const [text, qtype] = textQuery(() => {
    const r = Math.random();
    return r < 0.4 ? D.addresses : r < 0.65 ? D.places : r < 0.9 ? D.venues : D.zips;
  });
  get('search', 'api', { q: text, limit: 8, ...focus() }, qtype);
}

function structured() {
  // Photon has no structured endpoint: send the same fields as one line, which is what a client
  // would have to do. Tagged 'structured' so the comparison lines up with the other engines.
  const s = pick(D.structured);
  const line = [s.address, s.locality, s.region, s.postalcode].filter(Boolean).join(', ');
  get('structured', 'api', { q: line, limit: 8 });
}

function reverse() {
  const r = Math.random();
  const [lat, lon] = r < 0.6 ? pick(D.oa_points) : r < 0.9 ? pick(D.random_points) : pick(D.miss_points);
  get('reverse', 'reverse', { lat, lon, limit: 8 });
}

export default function () {
  const r = Math.random();
  if (r < 0.6) typeAhead();
  else if (r < 0.75) fullSearch();
  else if (r < 0.85) structured();
  else reverse();
  think();
}
