// k6 load test: simulated users of the Maine geocoder (see docs/LOAD_TEST_PLAN.md).
//
// Env:
//   BASE_URL   API origin (default http://127.0.0.1:4000); same script for Pelias and PostGIS
//   VUS        concurrent users for this run (constant)
//   DURATION   steady-state duration, e.g. 60s
//   WARMUP     warm-up duration excluded from thresholds via the "phase" tag, e.g. 15s
//
// One VU = one person. Session mix: 60% type-ahead, 15% full search, 10% structured,
// 15% reverse (map click), each followed by 3-8 s of think time.
// Query quality mix (text queries): 65% exact, 12% typo, 13% off-name variant, 10% miss;
// reverse: 90% on land, 10% offshore misses. Every request is tagged ep and qtype.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';

const BASE = (__ENV.BASE_URL || 'http://127.0.0.1:4000').replace(/\/+$/, '');
const VUS = Number(__ENV.VUS || 3);
const DURATION = __ENV.DURATION || '60s';
const WARMUP = __ENV.WARMUP || '15s';
// Only an edge checks keys; the engines' own ports, which the matrix runs against, do not.
const API_KEY = __ENV.API_KEY || '';

// One SharedArray per category: element access deserializes only that element.
const RAW = open('../corpus/maine_corpus.json');
const load = (key) => new SharedArray(key, () => JSON.parse(RAW)[key]);
const D = {
  addresses: load('addresses'), places: load('places'), venues: load('venues'),
  zips: load('zips'), structured: load('structured'), foci: load('foci'),
  oaPoints: load('oa_points'), randomPoints: load('random_points'),
  typos: load('typos'), variants: load('variants'), misses: load('misses'),
  missPoints: load('miss_points'),
};

function seconds(d) {
  const m = /^(\d+)(ms|s|m)$/.exec(d);
  return m ? Number(m[1]) * { ms: 0.001, s: 1, m: 60 }[m[2]] : 0;
}

export const options = {
  scenarios: {
    users: {
      executor: 'constant-vus',
      vus: VUS,
      duration: `${seconds(WARMUP) + seconds(DURATION)}s`,
      gracefulStop: '10s',
    },
  },
  // Thresholds double as sub-metric declarations so the summary reports per endpoint,
  // and they encode the SLOs. Only steady-state requests (phase:steady) count.
  thresholds: {
    'http_req_duration{ep:autocomplete,phase:steady}': ['p(95)<=250'],
    'http_req_duration{ep:search,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:structured,phase:steady}': ['p(95)<=750'],
    'http_req_duration{ep:reverse,phase:steady}': ['p(95)<=400'],
    'http_req_failed{phase:steady}': ['rate<0.01'],
    'http_reqs{phase:steady}': ['count>=0'],
    // Per query type (reporting only; SLOs are per endpoint).
    'http_req_duration{qtype:exact,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:typo,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:variant,phase:steady}': ['max>=0'],
    'http_req_duration{qtype:miss,phase:steady}': ['max>=0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
  noConnectionReuse: false,
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
  const res = http.get(`${BASE}/v1/${path}?${qs}`, {
    headers: API_KEY ? { 'X-API-Key': API_KEY } : {},
    tags: { ep, qtype, phase: phase(), name: `/v1/${path}` },
    timeout: '20s',
  });
  check(res, { 'status 200': (r) => r.status === 200 }, { ep });
  return res;
}

function focus() {
  const [lat, lon] = pick(D.foci);
  return { 'focus.point.lat': lat, 'focus.point.lon': lon };
}

// A text query and its quality type: exact 65%, typo 12%, variant 13%, miss 10%.
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
  // One request per keystroke from 2 characters, ~200 ms apart (widget debounce is 150 ms).
  for (let i = 2; i <= text.length; i += 1) {
    get('autocomplete', 'autocomplete', { text: text.slice(0, i), size: 8, ...f }, qtype);
    sleep(0.2);
  }
  if (Math.random() < 0.5) get('search', 'search', { text, size: 8, ...f }, qtype);
}

function fullSearch() {
  const [text, qtype] = textQuery(() => {
    const r = Math.random();
    return r < 0.4 ? D.addresses : r < 0.65 ? D.places : r < 0.9 ? D.venues : D.zips;
  });
  get('search', 'search', { text, size: 8, ...focus() }, qtype);
}

function structured() {
  const s = pick(D.structured);
  get('structured', 'search/structured', { ...s, size: 8 });
}

function reverse() {
  const r = Math.random();
  const qtype = r < 0.1 ? 'miss' : 'exact';
  const [lat, lon] = pick(r < 0.1 ? D.missPoints : r < 0.55 ? D.oaPoints : D.randomPoints);
  get('reverse', 'reverse', { 'point.lat': lat, 'point.lon': lon, size: 6 }, qtype);
}

export default function () {
  const r = Math.random();
  if (r < 0.6) typeAhead();
  else if (r < 0.75) fullSearch();
  else if (r < 0.85) structured();
  else reverse();
  think();
}
