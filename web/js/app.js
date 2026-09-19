// Pelias Maine demo: wires the map, the <pelias-search> element, structured search,
// reverse geocoding and the batch tool. Rendering uses DOM APIs and textContent only.
import { PeliasClient } from './pelias-client.js';
import './pelias-search.js';
import { createMap, makeMarker } from './map.js';
import { setupBatch } from './batch.js';
import { dms, fixed, distanceLabel } from './format.js';

const LAYERS = ['address', 'venue', 'street', 'locality', 'localadmin', 'neighbourhood', 'county', 'postalcode'];
const SOURCES = [
  ['openaddresses', 'OA'], ['openstreetmap', 'OSM'], ['whosonfirst', 'WOF'],
  ['gnis', 'GNIS'], ['overture', 'Overture'], ['zcta', 'ZCTA'],
];
// Zoom to use for point results without a bbox, by layer.
const ZOOM = { address: 17.5, venue: 16, street: 15, neighbourhood: 14, postalcode: 12,
  locality: 12, localadmin: 11, county: 8.5, region: 6.5 };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const client = new PeliasClient();

// ---- Engine switch (local development only) -----------------------------------------------
// The dev server injects <meta name="demo-engines" content="/engines.json">, a list of
// same-origin API prefixes (Pelias, pgeo SQL, pgeo FastAPI). Production serves the page
// without it, so the page makes no request and the switch stays hidden.
const ENGINE_KEY = 'pelias-demo-engine';
async function setupEngines() {
  if (document.querySelector('meta[name="demo-engines"]')?.content !== '/engines.json') return;
  let list;
  try {
    const res = await fetch('/engines.json', { headers: { Accept: 'application/json' } });
    if (!res.ok) return;
    list = (await res.json()).engines;
  } catch {
    return;
  }
  // Accept only same-origin path prefixes ('' or '/name'), never other origins.
  list = Array.isArray(list)
    ? list.filter((e) => e && typeof e.label === 'string' && typeof e.base === 'string'
        && /^(\/[a-z0-9-]{1,32})?$/.test(e.base)).slice(0, 8)
    : [];
  if (list.length < 2) return;
  const sel = $('#engine');
  for (const e of list) {
    const o = document.createElement('option');
    o.value = e.base;
    o.textContent = e.label.slice(0, 40);
    sel.append(o);
  }
  let saved = null;
  try { saved = localStorage.getItem(ENGINE_KEY); } catch { /* storage unavailable */ }
  if (saved !== null && list.some((e) => e.base === saved)) sel.value = saved;
  client.baseUrl = sel.value;
  sel.addEventListener('change', () => {
    client.baseUrl = sel.value;
    try { localStorage.setItem(ENGINE_KEY, sel.value); } catch { /* storage unavailable */ }
    for (const id of ['#search-results', '#structured-results', '#reverse-results']) $(id).replaceChildren();
    showDots([]);
  });
  $('#engine-pick').hidden = false;
}
const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
const map = createMap('map', { dark });
const marker = makeMarker('primary');
const probe = makeMarker('probe');

// ---- Result dots layer (all results of the last query) ------------------------------------

const EMPTY = { type: 'FeatureCollection', features: [] };
map.on('load', () => {
  map.addSource('results', { type: 'geojson', data: EMPTY });
  map.addLayer({
    id: 'results-halo',
    type: 'circle',
    source: 'results',
    paint: { 'circle-radius': 7, 'circle-color': dark ? '#111a29' : '#f3ead2', 'circle-opacity': 0.9 },
  });
  map.addLayer({
    id: 'results-dot',
    type: 'circle',
    source: 'results',
    paint: {
      'circle-radius': 4.5,
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-width': 2,
      'circle-stroke-color': dark ? '#e0579a' : '#b3246b',
    },
  });
  map.on('click', 'results-dot', (e) => {
    const gid = e.features?.[0]?.properties?.gid;
    const hit = lastFeatures.find((f) => f.properties?.gid === gid);
    if (hit) select(hit);
  });
  map.on('mouseenter', 'results-dot', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'results-dot', () => { map.getCanvas().style.cursor = ''; });
});

let lastFeatures = [];
function showDots(features) {
  lastFeatures = features;
  map.getSource('results')?.setData({ type: 'FeatureCollection', features });
}

// ---- Tabs ---------------------------------------------------------------------------------

const tabs = [...document.querySelectorAll('[role="tab"]')];
function activate(tab) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
  }
  map.getCanvas().style.cursor = tab.id === 'tab-reverse' ? 'crosshair' : '';
  clearSelection();
}
tabs.forEach((t, i) => {
  t.addEventListener('click', () => activate(t));
  t.addEventListener('keydown', (e) => {
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!d) return;
    const next = tabs[(i + d + tabs.length) % tabs.length];
    activate(next);
    next.focus();
  });
});
const current = () => tabs.find((t) => t.getAttribute('aria-selected') === 'true')?.id;

// ---- Filters ------------------------------------------------------------------------------

function buildChips(container, items, name) {
  for (const [value, label] of items) {
    const lab = el('label', 'chip');
    const input = el('input');
    input.type = 'checkbox';
    input.name = name;
    input.value = value;
    lab.append(input, el('span', null, label));
    container.append(lab);
  }
}
buildChips($('#layer-chips'), LAYERS.map((l) => [l, l]), 'layer');
buildChips($('#source-chips'), SOURCES, 'source');
const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);

function searchContext() {
  const ctx = { layers: checked('layer'), sources: checked('source') };
  const c = map.getCenter();
  if ($('#opt-focus').checked) ctx.focus = { lat: c.lat, lon: c.lng };
  if ($('#opt-bounds').checked) {
    const b = map.getBounds();
    ctx.boundary = { minLon: b.getWest(), minLat: b.getSouth(), maxLon: b.getEast(), maxLat: b.getNorth() };
  }
  return ctx;
}

// ---- Selected result card -------------------------------------------------------------------

const card = $('#result');

function meter(conf) {
  const m = el('span', `meter ${conf >= 0.8 ? 'hi' : conf < 0.5 ? 'lo' : ''}`);
  const on = Math.round((conf || 0) * 10);
  for (let i = 0; i < 10; i += 1) m.append(el('i', i < on ? 'on' : ''));
  return m;
}

function row(dl, term, value) {
  if (value == null || value === '') return;
  dl.append(el('dt', null, term));
  const dd = el('dd');
  if (value instanceof Node) dd.append(value);
  else dd.textContent = String(value);
  dl.append(dd);
}

function renderCard(feature, { reverse = false } = {}) {
  const p = feature.properties || {};
  const [lon, lat] = feature.geometry.coordinates;
  const [dlat, dlon] = dms(lat, lon);
  card.replaceChildren();

  const close = el('button', 'close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close result');
  close.addEventListener('click', clearSelection);
  card.append(close, el('span', `tag tag-${p.layer}`, p.layer), el('h2', null, p.label || p.name));

  const dl = el('dl');
  if (p.confidence != null) {
    const conf = el('span');
    conf.append(meter(p.confidence), document.createTextNode(Number(p.confidence).toFixed(2)));
    row(dl, 'Confidence', conf);
  } else {
    // Pelias scores confidence only for full searches, not for autocomplete suggestions.
    row(dl, 'Confidence', 'not scored for suggestions; press Enter for a full search');
  }
  row(dl, 'Match', p.match_type);
  row(dl, 'Accuracy', p.accuracy);
  row(dl, 'Source', `${p.source}${p.source_id ? ` #${p.source_id}` : ''}`);
  row(dl, 'Position', `${dlat}  ${dlon}`);
  row(dl, 'Decimal', `${fixed(lat)}, ${fixed(lon)}`);
  if (reverse && p.distance != null) row(dl, 'Distance', distanceLabel(p.distance));
  row(dl, 'Hierarchy', [p.neighbourhood, p.locality || p.localadmin, p.county, p.region_a, p.postalcode]
    .filter(Boolean).join(' · '));
  row(dl, 'GID', p.gid);
  card.append(dl);

  if (p.addendum) {
    const det = el('details');
    det.append(el('summary', null, 'Source attributes'), el('pre', null, JSON.stringify(p.addendum, null, 2)));
    card.append(det);
  }
  const raw = el('details');
  raw.append(el('summary', null, 'Raw GeoJSON'), el('pre', null, JSON.stringify(feature, null, 2)));
  card.append(raw);
  card.hidden = false;
}

function select(feature, { fly = true, reverse = false } = {}) {
  const [lon, lat] = feature.geometry.coordinates;
  if (!reverse) probe.remove();
  marker.setLngLat([lon, lat]).addTo(map);
  renderCard(feature, { reverse });
  if (!fly) return;
  const bbox = feature.bbox;
  if (bbox && (bbox[2] - bbox[0] > 0.002 || bbox[3] - bbox[1] > 0.002)) {
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: panelPadding(), maxZoom: 15, duration: 1200 });
  } else {
    map.flyTo({ center: [lon, lat], zoom: ZOOM[feature.properties?.layer] ?? 14, padding: panelPadding(), duration: 1200 });
  }
}

function clearSelection() {
  card.hidden = true;
  marker.remove();
}

// Keep the selected point out from under the cartouche.
function panelPadding() {
  const panel = document.querySelector('.cartouche').getBoundingClientRect();
  if (window.innerWidth <= 720) return { top: 40, left: 20, right: 20, bottom: panel.height + 20 };
  return { top: 40, left: panel.right + 20, right: 60, bottom: 40 };
}

// ---- Result lists -------------------------------------------------------------------------

function renderList(ol, features, { withDistance = false } = {}) {
  const opts = { reverse: withDistance };
  ol.replaceChildren();
  for (const f of features) {
    const p = f.properties || {};
    const li = el('li');
    li.tabIndex = 0;
    const meta = [p.layer, p.source, p.confidence != null ? `conf ${Number(p.confidence).toFixed(2)}` : null,
      withDistance && p.distance != null ? distanceLabel(p.distance) : null].filter(Boolean).join(' · ');
    li.append(el('span', 'r-label', p.label || p.name), el('span', `tag tag-${p.layer}`, p.layer), el('span', 'r-meta', meta));
    const pick = () => select(f, opts);
    li.addEventListener('click', pick);
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter') pick(); });
    ol.append(li);
  }
}

// ---- Search tab ---------------------------------------------------------------------------

const search = $('#search');
search.client = client;
search.context = searchContext;
search.addEventListener('pelias-results', (e) => {
  showDots(e.detail.features);
  if (e.detail.kind === 'search') renderList($('#search-results'), e.detail.features);
  else $('#search-results').replaceChildren();
});
search.addEventListener('pelias-select', (e) => select(e.detail.feature));
search.addEventListener('pelias-highlight', (e) => {
  const f = e.detail.feature;
  if (f) probe.setLngLat(f.geometry.coordinates).addTo(map);
});
search.addEventListener('focusout', () => probe.remove());

// ---- Structured tab -----------------------------------------------------------------------

$('#structured-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = new FormData(e.currentTarget);
  const fields = Object.fromEntries([...form.entries()].map(([k, v]) => [k, String(v)]));
  const ol = $('#structured-results');
  ol.replaceChildren(el('li', 'r-meta', 'Searching...'));
  try {
    const fc = await client.structured(fields, { size: 8 });
    showDots(fc.features);
    renderList(ol, fc.features);
    if (fc.features.length) select(fc.features[0]);
    else ol.replaceChildren(el('li', 'r-meta', 'No matches'));
  } catch (err) {
    ol.replaceChildren(el('li', 'r-meta', err.message));
  }
});

// ---- Reverse tab --------------------------------------------------------------------------

let reverseController = null;
async function reverseAt(lngLat) {
  reverseController?.abort();
  reverseController = new AbortController();
  const ol = $('#reverse-results');
  probe.setLngLat(lngLat).addTo(map);
  const layer = $('#rev-layer').value;
  const radius = $('#rev-radius').value;
  const [dlat, dlon] = dms(lngLat.lat, lngLat.lng);
  ol.replaceChildren(el('li', 'r-meta', `Sounding ${dlat} ${dlon}...`));
  try {
    const fc = await client.reverse(lngLat.lat, lngLat.lng, {
      size: 6,
      layers: layer ? [layer] : [],
      radiusKm: radius ? Number(radius) : undefined,
    }, reverseController.signal);
    showDots(fc.features);
    renderList(ol, fc.features, { withDistance: true });
    if (fc.features.length) select(fc.features[0], { fly: false, reverse: true });
    else ol.replaceChildren(el('li', 'r-meta', 'Nothing found within that radius'));
  } catch (err) {
    if (err.name !== 'AbortError') ol.replaceChildren(el('li', 'r-meta', err.message));
  }
}
map.on('click', (e) => {
  if (current() !== 'tab-reverse') return;
  if (map.queryRenderedFeatures(e.point, { layers: ['results-dot'] }).length) return;
  reverseAt(e.lngLat);
});
map.on('contextmenu', (e) => {
  activate($('#tab-reverse'));
  reverseAt(e.lngLat);
});

// ---- Batch tab ----------------------------------------------------------------------------

const SAMPLE = [
  'address',
  '389 Congress St, Portland, ME',
  '210 State St, Augusta, ME',
  '73 Harlow St, Bangor, ME',
  '32 Park St, Bar Harbor, ME',
  '100 Main St, Caribou, ME',
  'Moosehead Lake',
  'Katahdin',
  '04401',
].join('\n');
$('#batch-sample').addEventListener('click', () => { $('#batch-input').value = SAMPLE; });
setupBatch({
  client,
  textarea: $('#batch-input'),
  file: $('#batch-file'),
  run: $('#batch-run'),
  cancel: $('#batch-cancel'),
  download: $('#batch-download'),
  progress: $('#batch-progress'),
  status: $('#batch-status'),
  table: $('#batch-table'),
  context: () => ({ layers: checked('layer'), sources: checked('source') }),
  onStart: clearSelection,
  onResults: (features) => {
    showDots(features);
    if (features.length) {
      const b = features.reduce((acc, f) => {
        const [x, y] = f.geometry.coordinates;
        return [Math.min(acc[0], x), Math.min(acc[1], y), Math.max(acc[2], x), Math.max(acc[3], y)];
      }, [180, 90, -180, -90]);
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: panelPadding(), maxZoom: 13, duration: 1200 });
    }
  },
});

// ---- Coordinate readout and compass ticks ----------------------------------------------------

const roLat = $('#ro-lat');
const roLon = $('#ro-lon');
const roZoom = $('#ro-zoom');
function readout(lngLat) {
  const [a, b] = dms(lngLat.lat, lngLat.lng);
  roLat.textContent = a;
  roLon.textContent = b;
  roZoom.textContent = `Z ${map.getZoom().toFixed(1)}`;
}
map.on('mousemove', (e) => readout(e.lngLat));
map.on('moveend', () => readout(map.getCenter()));
map.on('load', () => readout(map.getCenter()));

const ticks = document.querySelector('.compass .c-ticks');
for (let deg = 0; deg < 360; deg += 10) {
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  const r1 = deg % 30 === 0 ? 46 : 50;
  const a = (deg * Math.PI) / 180;
  line.setAttribute('x1', String(Math.sin(a) * r1));
  line.setAttribute('y1', String(-Math.cos(a) * r1));
  line.setAttribute('x2', String(Math.sin(a) * 54));
  line.setAttribute('y2', String(-Math.cos(a) * 54));
  ticks.append(line);
}

const compass = document.querySelector('.compass');
map.on('rotate', () => { compass.style.transform = `rotate(${-map.getBearing()}deg)`; });

search.focus();

setupEngines();
