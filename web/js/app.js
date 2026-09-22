// Pelias Maine demo: wires the map, the <pelias-search> element, structured search,
// reverse geocoding and the batch tool. Rendering uses DOM APIs and textContent only.
import { PeliasClient, setApiKey, hasApiKey } from './pelias-client.js';
import './pelias-search.js';
import { createMap, loadRegion, makeMarker } from './map.js';
import { setupBatch } from './batch.js';
import { setupDemoTabs } from './demo-tabs.js';
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

// ---- API key ------------------------------------------------------------------------------
// The edge refuses /v1/* without a known key. The page holds one per tab; the form appears the
// first time the edge answers 401, and disappears once a request succeeds. The key is sent in a
// header by PeliasClient and appears nowhere on the page.
//
// The form is not shown on load, only on a 401: a deployment may sit behind a proxy that inserts
// the key itself (the public host does this, see infra/wharf/pelias.caddy), and there the page
// never sees a 401 and must not ask for a key it does not need.
const keyForm = $('#apikey-form');
const keyNote = $('#apikey-note');
function showKeyForm(message) {
  keyForm.hidden = false;
  keyNote.textContent = message || '';
  $('#apikey-input').focus();
}
keyForm.addEventListener('submit', (e) => {
  e.preventDefault();
  setApiKey($('#apikey-input').value);
  $('#apikey-input').value = '';
  keyNote.textContent = 'Checking...';
  client.autocomplete('portland', { size: 1 }).then(() => {
    keyForm.hidden = true;
    keyNote.textContent = '';
  }).catch((err) => {
    if (err.status === 401) { setApiKey(''); showKeyForm('That key was not accepted.'); }
    else keyNote.textContent = err.message;
  });
});
window.addEventListener('pelias-unauthorized', () => {
  showKeyForm(hasApiKey() ? 'Your API key is no longer accepted. Enter a current one.' : 'This service needs an API key.');
});

// ---- Engines ------------------------------------------------------------------------------
// The server announces its engines with <meta name="demo-engines" content="/engines.json">:
// a list of same-origin API prefixes ({label, base, kind}), e.g. Pelias at "" and pgeo at
// "/pgeo". Without the tag the page talks to Pelias only and hides the switch. The Address and
// Compare tabs need a pgeo engine (/v1/address exists only there).
const ENGINE_KEY = 'pelias-demo-engine';
let engines = [{ label: 'Pelias', base: '', kind: 'pelias' }];
let pgeoClient = null;
const engineOf = (base) => engines.find((e) => e.base === base) || engines[0];

async function setupEngines() {
  if (document.querySelector('meta[name="demo-engines"]')?.content === '/engines.json') {
    try {
      const res = await fetch('/engines.json', { headers: { Accept: 'application/json' } });
      if (res.ok) {
        const list = (await res.json()).engines;
        // Accept only same-origin path prefixes ('' or '/name'), never other origins.
        const clean = Array.isArray(list)
          ? list.filter((e) => e && typeof e.label === 'string' && typeof e.base === 'string'
              && /^(\/[a-z0-9-]{1,32})?$/.test(e.base)).slice(0, 8)
            .map((e) => ({ label: e.label.slice(0, 40), base: e.base,
              kind: e.kind === 'pgeo' || (e.kind == null && e.base !== '') ? 'pgeo' : 'pelias' }))
          : [];
        if (clean.length) engines = clean;
      }
    } catch { /* keep the Pelias-only default */ }
  }
  const pg = engines.find((e) => e.kind === 'pgeo');
  pgeoClient = pg ? new PeliasClient({ baseUrl: pg.base }) : null;
  setupExtras();
  // Point the shared client at the first announced engine before anything else: with a single
  // engine the switch below never runs, and a pgeo-only deployment serves nothing at /v1/*
  // unless pgeo is mounted there.
  client.baseUrl = engines[0].base;
  $('#engine-note').textContent = engines[0].kind === 'pgeo' ? 'PostgreSQL / PostGIS' : 'Elasticsearch';
  if (engines.length < 2) {
    // One engine: no switch to show, but say which one is answering.
    $('#engine-pick').hidden = false;
    $('#engine').hidden = true;
    $('#engine-only').textContent = engines[0].label;
    $('#engine-only').hidden = false;
    return;
  }
  const sel = $('#engine');
  for (const e of engines) {
    const o = document.createElement('option');
    o.value = e.base;
    o.textContent = e.label;
    sel.append(o);
  }
  let saved = null;
  try { saved = localStorage.getItem(ENGINE_KEY); } catch { /* storage unavailable */ }
  if (saved !== null && engines.some((e) => e.base === saved)) sel.value = saved;
  const apply = () => {
    client.baseUrl = sel.value;
    $('#engine-note').textContent = engineOf(sel.value).kind === 'pgeo' ? 'PostgreSQL / PostGIS' : 'Elasticsearch';
  };
  apply();
  sel.addEventListener('change', () => {
    apply();
    try { localStorage.setItem(ENGINE_KEY, sel.value); } catch { /* storage unavailable */ }
    for (const id of ['#search-results', '#structured-results', '#reverse-results']) $(id).replaceChildren();
    showDots([]);
    clearSelection();
    // the demo tabs hold the previous engine's answer too; tell them the engine changed
    for (const fn of tabListeners) fn('engine-change');
  });
  $('#engine-pick').hidden = false;
}

// The page is light only: the chart, its marks and the panel share one palette.
// The region decides the map extent, the basemap file and the page's own name, so it is read
// before the map is built. Top-level await: app.js is a module.
const region = await loadRegion();
document.documentElement.dataset.build = region.build || '';
document.title = region.name ? `${region.name} Geocoder` : 'Geocoder';
$('#region-name').textContent = region.name || '';
$('#region-kicker').textContent = region.name ? `United States \u00b7 ${region.name}` : 'United States';
$('#map').setAttribute('aria-label', region.name ? `Map of ${region.name}` : 'Map');
if (region.features) {
  $('#region-features').textContent = `${Number(region.features).toLocaleString('en-US')} features \u00b7 `;
}
const map = createMap('map', region);
const marker = makeMarker('primary');
const probe = makeMarker('probe');

// ---- Result dots layer (all results of the last query) ------------------------------------

const EMPTY = { type: 'FeatureCollection', features: [] };
map.on('load', () => {
  map.addSource('filter-circle', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'filter-circle-fill', type: 'fill', source: 'filter-circle',
    paint: { 'fill-color': '#b3246b', 'fill-opacity': 0.06 } });
  map.addLayer({ id: 'filter-circle-line', type: 'line', source: 'filter-circle',
    paint: { 'line-color': '#b3246b', 'line-width': 1.2, 'line-dasharray': [3, 2] } });
  map.addSource('addr-link', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'addr-link', type: 'line', source: 'addr-link',
    paint: { 'line-color': '#8a5a00', 'line-width': 2, 'line-dasharray': [2, 2] } });
  map.addSource('compare', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'compare-dot', type: 'circle', source: 'compare',
    paint: { 'circle-radius': ['case', ['==', ['get', 'rank'], 1], 7, 4.5],
      'circle-color': ['match', ['get', 'engine'], 'pelias', '#1f5f8b', '#c0392b'],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.9 } });
  map.addSource('results', { type: 'geojson', data: EMPTY });
  map.addLayer({
    id: 'results-halo',
    type: 'circle',
    source: 'results',
    paint: { 'circle-radius': 7, 'circle-color': '#f3ead2', 'circle-opacity': 0.9 },
  });
  map.addLayer({
    id: 'results-dot',
    type: 'circle',
    source: 'results',
    paint: {
      'circle-radius': 4.5,
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#b3246b',
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
  map.getCanvas().style.cursor = ['tab-reverse', 'tab-address', 'tab-nearby', 'tab-boundary'].includes(tab.id) ? 'crosshair' : '';
  clearSelection();
  drawCircle();
  if (tab.id !== 'tab-compare') map.getSource('compare')?.setData(EMPTY);
  if (tab.id !== 'tab-address') clearAddress();
  for (const fn of tabListeners) fn(tab.id);
}
const tabListeners = [];
tabs.forEach((t, i) => {
  t.addEventListener('click', () => activate(t));
  t.addEventListener('keydown', (e) => {
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!d) return;
    const shown = tabs.filter((x) => !x.hidden);
    const k = shown.indexOf(t);
    const next = shown[(k + d + shown.length) % shown.length];
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

let areaGid = null; // boundary.gid chosen in "Only in town or county"

function searchContext() {
  const ctx = { layers: checked('layer'), sources: checked('source') };
  const c = map.getCenter();
  if ($('#opt-focus').checked) ctx.focus = { lat: c.lat, lon: c.lng };
  if ($('#opt-bounds').checked) {
    const b = map.getBounds();
    ctx.boundary = { minLon: b.getWest(), minLat: b.getSouth(), maxLon: b.getEast(), maxLat: b.getNorth() };
  }
  if ($('#opt-circle').checked) ctx.circle = { lat: c.lat, lon: c.lng, radiusKm: Number($('#opt-radius').value) };
  if (areaGid) ctx.gid = areaGid;
  const cats = $('#opt-categories').value.split(',').map((x) => x.trim().toLowerCase()).filter(Boolean);
  if (cats.length) ctx.categories = cats.slice(0, 10);
  return ctx;
}

// Circle filter drawn on the map (64-sided polygon; follows the map center).
function circlePolygon(lat, lon, km) {
  const pts = [];
  for (let i = 0; i <= 64; i += 1) {
    const a = (i / 64) * 2 * Math.PI;
    const dLat = (km / 111.32) * Math.cos(a);
    const dLon = (km / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a);
    pts.push([lon + dLon, lat + dLat]);
  }
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [pts] }, properties: {} };
}
function drawCircle() {
  const src = map.getSource('filter-circle');
  if (!src) return;
  const on = $('#opt-circle').checked && current() === 'tab-search';
  const c = map.getCenter();
  src.setData(on ? circlePolygon(c.lat, c.lng, Number($('#opt-radius').value)) : EMPTY);
}
$('#opt-circle').addEventListener('change', drawCircle);
$('#opt-radius').addEventListener('change', drawCircle);
map.on('move', () => { if ($('#opt-circle').checked) drawCircle(); });

// "Only in town or county": suggestions from the current engine, restricted to admin layers.
let areaTimer = null;
let areaCtl = null;
function chooseArea(f) {
  areaGid = f ? f.properties.gid : null;
  $('#area-chosen').hidden = !f;
  $('#area-chosen').textContent = f ? `Only in ${f.properties.label}` : '';
  $('#area-clear').hidden = !f;
  $('#area-list').hidden = true;
  $('#area-input').value = '';
}
$('#area-clear').addEventListener('click', () => chooseArea(null));
$('#area-input').addEventListener('input', () => {
  clearTimeout(areaTimer);
  const text = $('#area-input').value.trim();
  if (text.length < 2) { $('#area-list').hidden = true; return; }
  areaTimer = setTimeout(async () => {
    areaCtl?.abort();
    areaCtl = new AbortController();
    try {
      const fc = await client.autocomplete(text, { size: 6, layers: ['locality', 'localadmin', 'county'] }, areaCtl.signal);
      const ul = $('#area-list');
      ul.replaceChildren();
      for (const f of fc.features) {
        const li = el('li', null, `${f.properties.label} (${f.properties.layer})`);
        li.tabIndex = 0;
        li.addEventListener('click', () => chooseArea(f));
        li.addEventListener('keydown', (e) => { if (e.key === 'Enter') chooseArea(f); });
        ul.append(li);
      }
      ul.hidden = fc.features.length === 0;
    } catch (err) {
      if (err.name !== 'AbortError') $('#area-list').hidden = true;
    }
  }, 200);
});

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
  if (engines.length > 1) {
    row(dl, 'Engine', `${engineOf(client.baseUrl).label}${client.lastMs != null ? ` · ${client.lastMs} ms` : ''}`);
  }
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

// ---- Address tab (pgeo /v1/address: USPS Publication 28) -----------------------------------

const addrSearch = $('#addr-search');
const addrCard = $('#addr-result');
const addrStatus = $('#addr-status');
const addrPoint = makeMarker('probe');
let addrCtl = null;
let bestCtl = null;
let bestTimer = null;

function setupExtras() {
  const hasPgeo = !!pgeoClient;
  const hasBoth = hasPgeo && engines.some((e) => e.kind === 'pelias');
  $('#tab-address').hidden = !hasPgeo;
  $('#tab-compare').hidden = !hasBoth;
  if (hasPgeo) {
    addrSearch.client = pgeoClient; // suggestions and addresses from the same engine (gids match)
    addrSearch.context = () => { const c = map.getCenter(); return { focus: { lat: c.lat, lon: c.lng } }; };
  }
  setupDemoTabs({ map, client, pgeoClient, meter, row, current, panelPadding, onTab: (fn) => tabListeners.push(fn) });
}

function clearAddress() {
  addrCard.hidden = true;
  addrPoint.remove();
  map.getSource('addr-link')?.setData(EMPTY);
}

function copyButton(text) {
  const b = el('button', 'btn copy', 'Copy');
  b.type = 'button';
  b.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
      b.textContent = 'Copied';
    } catch {
      b.textContent = 'Copy failed';
    }
    setTimeout(() => { b.textContent = 'Copy'; }, 1500);
  });
  return b;
}

function renderAddress(feature) {
  const p = feature.properties || {};
  const u = p.usps;
  const pl = p.place || {};
  addrCard.replaceChildren();
  const head = el('div', 'usps-head');
  const badge = !u ? el('span', 'badge place', 'place only')
    : u.match === 'nearest' ? el('span', 'badge nearest', `nearest address · ${Math.round(u.distance_m ?? 0)} m`)
      : el('span', 'badge exact', u.match === 'fallback' ? 'best match' : 'exact');
  head.append(badge, el('h2', null, p.label || pl.name || ''));
  addrCard.append(head);
  if (u?.delivery_line) {
    const block = `${u.delivery_line}\n${u.last_line || ''}`;
    const pre = el('pre', 'usps-block', block);
    const wrap = el('div', 'usps-wrap');
    wrap.append(pre, copyButton(block));
    addrCard.append(wrap);
    const dl = el('dl', 'usps-parts');
    row(dl, 'Number', u.primary_number);
    row(dl, 'Pre-dir.', u.predirectional);
    row(dl, 'Street', u.street_name);
    row(dl, 'Suffix', u.suffix);
    row(dl, 'Modifier', u.post_modifier);
    row(dl, 'Post-dir.', u.postdirectional);
    row(dl, 'Unit', [u.secondary_designator, u.secondary_number].filter(Boolean).join(' '));
    row(dl, 'City', u.city);
    row(dl, 'State', u.state);
    row(dl, 'ZIP', u.zip5);
    addrCard.append(dl);
  } else {
    addrCard.append(el('p', 'hint', 'No street address: a town, county or ZIP has no single mailing address. Place details below.'));
  }
  const dl2 = el('dl');
  row(dl2, 'Municipality', pl.municipality);
  row(dl2, 'County', pl.county && `${pl.county}${pl.county_fips ? ` (FIPS ${pl.county_fips})` : ''}`);
  row(dl2, 'State', pl.state && `${pl.state} (FIPS ${pl.state_fips})`);
  row(dl2, 'Location', `${fixed(pl.lat)}, ${fixed(pl.lon)}`);
  if (p.confidence != null) {
    const conf = el('span');
    conf.append(meter(p.confidence), document.createTextNode(Number(p.confidence).toFixed(2)));
    row(dl2, 'Confidence', conf);
  }
  row(dl2, 'Source', u ? `${u.source} · ${u.gid}` : p.gid);
  if (pgeoClient?.lastMs != null) row(dl2, 'Response', `${pgeoClient.lastMs} ms (pgeo)`);
  addrCard.append(dl2);
  const raw = el('details');
  raw.append(el('summary', null, 'Raw response'), el('pre', null, JSON.stringify(feature, null, 2)));
  addrCard.append(raw);
  addrCard.hidden = false;

  // map: the place, and for a nearest match the address point with a dashed link
  const [lon, lat] = feature.geometry.coordinates;
  marker.setLngLat([lon, lat]).addTo(map);
  if (u && u.match === 'nearest' && u.lat != null) {
    addrPoint.setLngLat([u.lon, u.lat]).addTo(map);
    map.getSource('addr-link')?.setData({ type: 'Feature', properties: {},
      geometry: { type: 'LineString', coordinates: [[lon, lat], [u.lon, u.lat]] } });
  } else {
    addrPoint.remove();
    map.getSource('addr-link')?.setData(EMPTY);
  }
  map.flyTo({ center: [lon, lat], zoom: u ? 17 : 12, padding: panelPadding(), duration: 1000 });
}

async function lookupAddress(q) {
  if (!pgeoClient) return;
  addrCtl?.abort();
  addrCtl = new AbortController();
  const unit = $('#addr-unit').value.trim();
  addrStatus.textContent = 'Looking up...';
  try {
    const fc = await pgeoClient.address({ ...q, unit: unit || undefined }, addrCtl.signal);
    const f = fc.features?.[0];
    if (!f) { addrStatus.textContent = 'No match.'; clearAddress(); return; }
    addrStatus.textContent = '';
    renderAddress(f);
  } catch (err) {
    if (err.name !== 'AbortError') addrStatus.textContent = err.message;
  }
}

// A suggestion: by gid; interpolated addresses have no stored record, so use their label.
addrSearch.addEventListener('pelias-select', (e) => {
  const p = e.detail.feature.properties || {};
  if (p.source === 'interpolation' || !/^[a-z0-9_]+:[a-z_]+:[A-Za-z0-9_\/.:-]{1,160}$/.test(p.gid || '')) {
    lookupAddress({ text: p.label });
  } else {
    lookupAddress({ ids: p.gid });
  }
});
// While typing: the best full-search match with its confidence, offered as a one-click pick.
addrSearch.addEventListener('pelias-results', (e) => {
  clearTimeout(bestTimer);
  const text = (e.detail.text || '').trim();
  if (e.detail.kind !== 'autocomplete' || text.length < 3 || !pgeoClient) return;
  bestTimer = setTimeout(async () => {
    bestCtl?.abort();
    bestCtl = new AbortController();
    try {
      const c = map.getCenter();
      const fc = await pgeoClient.search(text, { size: 1, focus: { lat: c.lat, lon: c.lng } }, bestCtl.signal);
      const f = fc.features?.[0];
      addrStatus.replaceChildren();
      if (!f) return;
      const chip = el('button', 'best', '');
      chip.type = 'button';
      chip.append(el('span', 'best-k', 'Best match'), el('span', 'best-l', f.properties.label),
        el('span', 'best-c', `conf ${Number(f.properties.confidence ?? 0).toFixed(2)}`));
      chip.addEventListener('click', () => addrSearch.dispatchEvent(new CustomEvent('pelias-select', { detail: { feature: f } })));
      addrStatus.append(chip);
    } catch { /* superseded or offline: no preview */ }
  }, 400);
});
$('#addr-find').addEventListener('click', () => {
  const text = (addrSearch.value || '').trim();
  if (text) lookupAddress({ text });
});
map.on('click', (e) => {
  if (current() !== 'tab-address' || !pgeoClient) return;
  lookupAddress({ lat: e.lngLat.lat, lon: e.lngLat.lng, radiusKm: 0.3 });
});

// ---- Compare tab (the same search on Pelias and on pgeo) ------------------------------------

function haversineM(a, b) {
  const r = (d) => (d * Math.PI) / 180;
  const dLat = r(b[1] - a[1]);
  const dLon = r(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a[1])) * Math.cos(r(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.sqrt(h));
}

$('#compare-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#compare-text').value.trim();
  if (!text || !pgeoClient) return;
  const pel = engines.find((x) => x.kind === 'pelias');
  const pg = engines.find((x) => x.kind === 'pgeo');
  const sides = [{ eng: pel, key: 'pelias', c: new PeliasClient({ baseUrl: pel.base }) },
    { eng: pg, key: 'pgeo', c: new PeliasClient({ baseUrl: pg.base }) }];
  const cols = $('#compare-cols');
  cols.replaceChildren();
  $('#compare-summary').textContent = 'Asking both engines...';
  const c = map.getCenter();
  const out = await Promise.all(sides.map(async (sd) => {
    try {
      const fc = await sd.c.search(text, { size: 5, focus: { lat: c.lat, lon: c.lng } });
      return { ...sd, features: fc.features || [], ms: sd.c.lastMs };
    } catch (err) {
      return { ...sd, features: [], ms: sd.c.lastMs, error: err.message };
    }
  }));
  const dots = [];
  for (const sd of out) {
    const col = el('div', `cmp-col cmp-${sd.key}`);
    col.append(el('h3', null, sd.eng.label), el('p', 'r-meta', sd.error ? sd.error : `${sd.features.length} results · ${sd.ms} ms`));
    const ol = el('ol', 'results');
    sd.features.forEach((f, i) => {
      const p = f.properties || {};
      const li = el('li');
      li.tabIndex = 0;
      li.append(el('span', 'r-label', p.label || p.name), el('span', `tag tag-${p.layer}`, p.layer),
        el('span', 'r-meta', `conf ${p.confidence != null ? Number(p.confidence).toFixed(2) : '-'} · ${p.source}`));
      li.addEventListener('click', () => select(f));
      ol.append(li);
      dots.push({ ...f, properties: { ...p, engine: sd.key, rank: i + 1 } });
    });
    col.append(ol);
    cols.append(col);
  }
  map.getSource('compare')?.setData({ type: 'FeatureCollection', features: dots });
  const [a, b] = out.map((sd) => sd.features[0]);
  let msg;
  if (a && b) {
    const d = haversineM(a.geometry.coordinates, b.geometry.coordinates);
    msg = d < 250 ? `First results agree (${Math.round(d)} m apart).`
      : `First results differ: ${d < 1000 ? `${Math.round(d)} m` : `${(d / 1000).toFixed(1)} km`} apart.`;
  } else {
    msg = a || b ? 'Only one engine found something.' : 'Neither engine found anything.';
  }
  $('#compare-summary').textContent = msg;
  if (dots.length) {
    const bb = dots.reduce((acc, f) => {
      const [x, y] = f.geometry.coordinates;
      return [Math.min(acc[0], x), Math.min(acc[1], y), Math.max(acc[2], x), Math.max(acc[3], y)];
    }, [180, 90, -180, -90]);
    map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: panelPadding(), maxZoom: 14, duration: 1000 });
  }
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
