// Four tabs that show pgeo doing the things the study measured: a form that fills itself from
// one best candidate, the confidence score made visible, search restricted to a drawn area, and
// "what's here" as a dispatcher would ask it. Same rules as the rest of the page: DOM APIs and
// textContent only (the security suite checks), every value a bound query parameter, nothing
// loaded from anywhere but this origin.
//
// setupDemoTabs(ctx) is called once by app.js after the engines are known. ctx carries the map,
// the clients and the small helpers app.js already has, so nothing here is duplicated.
import { maplibregl, makeMarker } from './map.js';
import { fixed, distanceLabel } from './format.js';

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const EMPTY = { type: 'FeatureCollection', features: [] };

// Below this the Confirm button is disabled: the engine is saying "here is a result, but I do not
// believe it" (report Section 3.1.1), and a form should not be filled from a guess.
const CONFIRM_MIN = 0.5;

export function setupDemoTabs(ctx) {
  const { map, client, pgeoClient, meter, row, current, panelPadding, onTab } = ctx;
  setupFormFiller({ map, pgeoClient, meter, row, panelPadding, onTab });
  setupConfidence({ map, client, meter, row, panelPadding, onTab });
  setupBoundary({ map, client, meter, current, panelPadding, onTab });
  setupNearby({ map, client, current, panelPadding, onTab });
}

// ---- Form filler --------------------------------------------------------------------------------
// One best candidate per keystroke (size=1), shown on the map with a callout that updates as the
// address refines; Confirm asks pgeo's /v1/address for the USPS parts and fills the contact form.

function setupFormFiller({ map, pgeoClient, meter, row, panelPadding, onTab }) {
  const tab = $('#tab-form');
  if (!pgeoClient) { tab.hidden = true; return; }   // /v1/address is pgeo only
  const search = $('#form-search');
  const confirm = $('#form-confirm');
  const status = $('#form-status');
  const form = $('#contact-form');
  const marker = makeMarker('primary');
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 18, maxWidth: '260px' });
  let best = null;      // the current best candidate (a GeoJSON feature)
  let bestCtl = null;
  let timer = null;

  search.client = pgeoClient;
  search.context = () => { const c = map.getCenter(); return { focus: { lat: c.lat, lon: c.lng } }; };

  function calloutFor(f, conf) {
    const p = f.properties || {};
    const box = el('div', 'callout');
    box.append(el('strong', null, p.name || p.label || ''));
    const line = [p.housenumber, p.street].filter(Boolean).join(' ');
    if (line && line !== p.name) box.append(el('div', null, line));
    const town = [p.locality || p.localadmin, [p.region_a, p.postalcode].filter(Boolean).join(' ')].filter(Boolean).join(', ');
    if (town) box.append(el('div', null, town));
    const c = el('div', 'callout-conf');
    if (conf != null) c.append(meter(conf), el('span', null, `confidence ${conf.toFixed(2)}`));
    else c.append(el('span', null, `type-ahead match · ${p.layer} · ${p.source}`));
    box.append(c);
    return box;
  }

  function showBest(f, { conf = null } = {}) {
    best = f;
    if (!f) { marker.remove(); popup.remove(); confirm.disabled = true; status.textContent = ''; return; }
    const [lon, lat] = f.geometry.coordinates;
    marker.setLngLat([lon, lat]).addTo(map);
    popup.setLngLat([lon, lat]).setDOMContent(calloutFor(f, conf)).addTo(map);
    map.easeTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 13), padding: panelPadding(), duration: 500 });
    // Confirm is gated on confidence only when a confidence is known and low: the engine is then
    // saying "here is a result, but I do not believe it", and a form should not be filled from a
    // guess. A type-ahead match with no score yet is offered, and /v1/address resolves it exactly.
    const uncertain = conf != null && conf < CONFIRM_MIN;
    confirm.disabled = uncertain;
    status.textContent = uncertain ? `Best candidate is uncertain (confidence ${conf.toFixed(2)}). Keep typing.` : '';
  }

  // The point follows the top type-ahead result on every keystroke - no extra request; the
  // dropdown already has it. Full search on a half-typed query finds nothing ("389 congress st
  // port" parses as street "congress st port"), so a confidence is only asked for after a pause,
  // and attached to the callout when search agrees on the same place.
  search.addEventListener('pelias-results', (e) => {
    clearTimeout(timer);
    const text = (e.detail.text || '').trim();
    const top = e.detail.features?.[0] || null;
    if (text.length < 3 || !top) { showBest(null); return; }
    showBest(top);
    timer = setTimeout(async () => {
      bestCtl?.abort();
      bestCtl = new AbortController();
      try {
        const c = map.getCenter();
        const fc = await pgeoClient.search(text, { size: 1, focus: { lat: c.lat, lon: c.lng } }, bestCtl.signal);
        const f = fc.features?.[0];
        if (f && best && f.properties?.gid === best.properties?.gid) showBest(best, { conf: Number(f.properties.confidence ?? 0) });
      } catch (err) {
        if (err.name !== 'AbortError') status.textContent = err.message;
      }
    }, 600);
  });
  // Picking a suggestion from the dropdown makes it the candidate outright.
  search.addEventListener('pelias-select', (e) => { clearTimeout(timer); showBest(e.detail.feature); });

  const set = (name, value) => { const i = form.elements[name]; if (i) i.value = value ?? ''; };

  confirm.addEventListener('click', async () => {
    if (!best) return;
    const p = best.properties || {};
    status.textContent = 'Fetching the USPS address...';
    confirm.disabled = true;
    try {
      const q = /^[a-z0-9_]+:[a-z_]+:[A-Za-z0-9_\/.:-]{1,160}$/.test(p.gid || '') && p.source !== 'interpolation'
        ? { ids: p.gid } : { text: p.label };
      const fc = await pgeoClient.address(q);
      const a = fc.features?.[0];
      const u = a?.properties?.usps;
      const pl = a?.properties?.place || {};
      const [lon, lat] = (a || best).geometry.coordinates;
      set('venue', p.layer === 'venue' ? (p.name || '') : '');   // an address has no business name
      set('street', u?.delivery_line || [p.housenumber, p.street].filter(Boolean).join(' '));
      set('city', u?.city || p.locality || p.localadmin || pl.municipality || '');
      set('state', u?.state || p.region_a || 'ME');
      set('zip', u?.zip5 || p.postalcode || '');
      set('lat', fixed(lat, 6));
      set('lon', fixed(lon, 6));
      status.replaceChildren();
      const note = el('span');
      note.append(u ? el('span', 'badge exact', u.match === 'nearest' ? `nearest address, ${Math.round(u.distance_m ?? 0)} m away` : 'USPS Publication 28')
        : el('span', 'badge place', 'no street address for this place'));
      note.append(document.createTextNode(` filled from ${p.source || 'pgeo'}`));
      status.append(note);
      form.classList.add('filled');
      setTimeout(() => form.classList.remove('filled'), 1200);
    } catch (err) {
      status.textContent = err.message;
    } finally {
      confirm.disabled = false;
    }
  });
  $('#form-clear').addEventListener('click', () => {
    form.reset(); status.textContent = ''; search.value = ''; showBest(null);
  });
  onTab((id) => { if (id !== 'tab-form') { marker.remove(); popup.remove(); } });
}

// ---- Confidence explorer -----------------------------------------------------------------------
// Every candidate with its confidence bar, and why the top ones share a low score when they do:
// the engine divides confidence among tied distinct places (report Section 3.1.1).

function engineLabel() {
  const sel = document.querySelector('#engine');
  if (sel && !sel.hidden && sel.selectedOptions[0]) return sel.selectedOptions[0].textContent;
  return document.querySelector('#engine-only')?.textContent || 'the engine';
}

function setupConfidence({ map, client, meter, row, panelPadding, onTab }) {
  const form = $('#conf-form');
  const out = $('#conf-results');
  const note = $('#conf-note');
  const src = () => map.getSource('conf-dots');
  map.on('load', () => {
    map.addSource('conf-dots', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'conf-dots', type: 'circle', source: 'conf-dots',
      paint: { 'circle-radius': ['interpolate', ['linear'], ['get', 'confidence'], 0, 4, 1, 11],
        'circle-color': ['interpolate', ['linear'], ['get', 'confidence'], 0.3, '#c0392b', 0.6, '#e0a800', 0.9, '#2e8b57'],
        'circle-opacity': 0.75, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2 } });
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('#conf-text').value.trim();
    if (!text) return;
    out.replaceChildren();
    note.textContent = 'Asking...';
    try {
      const c = map.getCenter();
      const useFocus = $('#conf-focus').checked;
      const fc = await client.search(text, { size: 10, ...(useFocus ? { focus: { lat: c.lat, lon: c.lng } } : {}) });
      const feats = fc.features || [];
      src()?.setData({ type: 'FeatureCollection', features: feats });
      if (!feats.length) { note.textContent = 'No candidates: the engine returns nothing rather than a guess.'; return; }
      const top = Number(feats[0].properties.confidence ?? 0);
      const tied = feats.filter((f) => Number(f.properties.confidence ?? 0) >= top - 0.02);
      // distinct places among the ties, on the same ~1 km grid the engine uses
      const cells = new Set(tied.map((f) => f.geometry.coordinates.map((v) => v.toFixed(2)).join(',')));
      note.replaceChildren();
      const who = el('span', 'who', `${engineLabel()} says: `);
      note.append(who);
      if (cells.size > 1 && top < 0.8) {
        note.append(el('span', 'badge nearest', 'ambiguous'),
          document.createTextNode(` ${cells.size} different places tie for first, so confidence is divided among them: ${top.toFixed(2)} each. The query cannot say which is meant.`));
      } else if (cells.size > 1) {
        // The other way an engine can answer an ambiguous query: call every candidate certain.
        note.append(el('span', 'badge place', 'no doubt expressed'),
          document.createTextNode(` ${cells.size} different places all scored ${top.toFixed(2)}. This engine reports them as equally certain and leaves the choice to you.`));
      } else if (top >= 0.8) {
        note.append(el('span', 'badge exact', 'confident'), document.createTextNode(` one clear answer at ${top.toFixed(2)}`));
      } else {
        note.append(el('span', 'badge place', 'weak'), document.createTextNode(` best guess only ${top.toFixed(2)}: a partial or fuzzy match.`));
      }
      feats.forEach((f, i) => {
        const p = f.properties;
        const li = el('li', 'conf-row');
        const body = el('div');
        body.append(el('div', 'r-label', p.label || p.name));
        const m = el('div', 'r-meta');
        m.append(meter(p.confidence ?? 0), el('span', null, ` ${Number(p.confidence ?? 0).toFixed(2)} · ${p.match_type || ''} · ${p.layer} · ${p.source}`));
        body.append(m);
        li.append(body);
        li.addEventListener('click', () => {
          const [lon, lat] = f.geometry.coordinates;
          map.flyTo({ center: [lon, lat], zoom: 14, padding: panelPadding(), duration: 700 });
        });
        out.append(li);
      });
      if (feats.length > 1) {
        const b = bounds(feats);
        map.fitBounds(b, { padding: { ...panelPadding(), top: 60, bottom: 60 }, maxZoom: 14, duration: 700 });
      }
    } catch (err) {
      note.textContent = err.message;
    }
  });
  onTab((id) => {
    if (id !== 'tab-confidence') src()?.setData(EMPTY);
    if (id === 'engine-change') { src()?.setData(EMPTY); out.replaceChildren(); note.textContent = ''; }
  });
}

// ---- Boundary search ---------------------------------------------------------------------------
// Draw a rectangle (drag) or a circle (click, then radius) on the map; results are restricted to
// it. This is the filtered request path - boundary.rect and boundary.circle - which no other tab
// exercises, and the question a dispatcher actually asks: "Main Street, in *this* town".

function setupBoundary({ map, client, meter, current, panelPadding, onTab }) {
  const status = $('#bnd-status');
  const out = $('#bnd-results');
  const modeSel = $('#bnd-mode');
  let shape = null;          // { kind: 'rect', minLon.. } | { kind: 'circle', lat, lon, radiusKm }
  let dragStart = null;
  const src = () => map.getSource('bnd-shape');
  const dots = () => map.getSource('bnd-dots');
  map.on('load', () => {
    map.addSource('bnd-shape', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'bnd-fill', type: 'fill', source: 'bnd-shape', paint: { 'fill-color': '#1f5f8b', 'fill-opacity': 0.08 } });
    map.addLayer({ id: 'bnd-line', type: 'line', source: 'bnd-shape', paint: { 'line-color': '#1f5f8b', 'line-width': 2 } });
    map.addSource('bnd-dots', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'bnd-dots', type: 'circle', source: 'bnd-dots',
      paint: { 'circle-radius': 5.5, 'circle-color': '#1f5f8b', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } });
  });

  function rectFeature(a, b) {
    const minLon = Math.min(a.lng, b.lng); const maxLon = Math.max(a.lng, b.lng);
    const minLat = Math.min(a.lat, b.lat); const maxLat = Math.max(a.lat, b.lat);
    return { type: 'Feature', properties: {}, geometry: { type: 'Polygon',
      coordinates: [[[minLon, minLat], [maxLon, minLat], [maxLon, maxLat], [minLon, maxLat], [minLon, minLat]]] } };
  }
  function circleFeature(lat, lon, km) {
    const pts = [];
    for (let i = 0; i <= 64; i += 1) {
      const a = (i / 64) * 2 * Math.PI;
      pts.push([lon + (km / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a), lat + (km / 111.32) * Math.cos(a)]);
    }
    return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [pts] } };
  }
  function describe() {
    if (!shape) return 'No area yet.';
    if (shape.kind === 'circle') return `Circle: ${shape.radiusKm} km around ${fixed(shape.lat, 4)}, ${fixed(shape.lon, 4)}`;
    return `Rectangle: ${fixed(shape.minLat, 3)}..${fixed(shape.maxLat, 3)} N, ${fixed(shape.minLon, 3)}..${fixed(shape.maxLon, 3)} W`;
  }

  // Drawing. Rectangle: drag with the pointer while this tab is active (map panning is suspended
  // during the drag). Circle: click a centre; radius from the select.
  const active = () => current() === 'tab-boundary';
  map.on('mousedown', (e) => {
    if (!active() || modeSel.value !== 'rect' || e.originalEvent.button !== 0) return;
    dragStart = e.lngLat; map.dragPan.disable();
  });
  map.on('mousemove', (e) => {
    if (!dragStart) return;
    src()?.setData(rectFeature(dragStart, e.lngLat));
  });
  map.on('mouseup', (e) => {
    if (!dragStart) return;
    const a = dragStart; dragStart = null; map.dragPan.enable();
    if (Math.abs(a.lng - e.lngLat.lng) < 1e-4 || Math.abs(a.lat - e.lngLat.lat) < 1e-4) return;
    shape = { kind: 'rect', minLon: Math.min(a.lng, e.lngLat.lng), maxLon: Math.max(a.lng, e.lngLat.lng),
      minLat: Math.min(a.lat, e.lngLat.lat), maxLat: Math.max(a.lat, e.lngLat.lat) };
    src()?.setData(rectFeature(a, e.lngLat));
    status.textContent = describe();
  });
  map.on('click', (e) => {
    if (!active() || modeSel.value !== 'circle') return;
    shape = { kind: 'circle', lat: e.lngLat.lat, lon: e.lngLat.lng, radiusKm: Number($('#bnd-radius').value) };
    src()?.setData(circleFeature(shape.lat, shape.lon, shape.radiusKm));
    status.textContent = describe();
  });
  $('#bnd-radius').addEventListener('change', () => {
    if (shape?.kind === 'circle') { shape.radiusKm = Number($('#bnd-radius').value); src()?.setData(circleFeature(shape.lat, shape.lon, shape.radiusKm)); status.textContent = describe(); }
  });
  $('#bnd-clear').addEventListener('click', () => { shape = null; src()?.setData(EMPTY); dots()?.setData(EMPTY); out.replaceChildren(); status.textContent = describe(); });

  $('#bnd-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('#bnd-text').value.trim();
    if (!text) return;
    if (!shape) { status.textContent = 'Draw an area on the map first.'; return; }
    out.replaceChildren();
    status.textContent = 'Searching inside the area...';
    const opts = { size: 10 };
    if (shape.kind === 'rect') opts.boundary = { minLon: shape.minLon, minLat: shape.minLat, maxLon: shape.maxLon, maxLat: shape.maxLat };
    else opts.circle = { lat: shape.lat, lon: shape.lon, radiusKm: shape.radiusKm };
    try {
      const [inside, everywhere] = await Promise.all([
        client.search(text, opts),
        client.search(text, { size: 1 }),
      ]);
      const feats = inside.features || [];
      dots()?.setData({ type: 'FeatureCollection', features: feats });
      const first = everywhere.features?.[0]?.properties?.label;
      status.replaceChildren(el('span', 'badge exact', `${feats.length} inside`), document.createTextNode(' the area.'));
      if (first && feats[0] && first !== feats[0].properties.label) {
        status.append(document.createTextNode(` Without it, the first answer statewide would have been ${first}.`));
      }
      feats.forEach((f, i) => {
        const p = f.properties;
        const li = el('li', 'conf-row');
        const body = el('div');
        body.append(el('div', 'r-label', p.label || p.name));
        const m = el('div', 'r-meta');
        m.append(meter(p.confidence ?? 0), el('span', null, ` ${Number(p.confidence ?? 0).toFixed(2)} · ${p.layer}`));
        body.append(m);
        li.append(body);
        li.addEventListener('click', () => map.flyTo({ center: f.geometry.coordinates, zoom: 15, padding: panelPadding(), duration: 600 }));
        out.append(li);
      });
    } catch (err) {
      status.textContent = err.message;
    }
  });
  onTab((id) => {
    if (id === 'engine-change') { dots()?.setData(EMPTY); out.replaceChildren(); status.textContent = describe(); return; }
    if (id !== 'tab-boundary') { src()?.setData(EMPTY); dots()?.setData(EMPTY); if (dragStart) { dragStart = null; map.dragPan.enable(); } }
    else status.textContent = describe();
  });
}

// ---- Nearby / what's here ----------------------------------------------------------------------
// Click the map: the address at that point, then everything within the radius ranked by distance,
// grouped by kind. Reverse geocoding the way it is actually used.

function setupNearby({ map, client, current, panelPadding, onTab }) {
  const out = $('#near-results');
  const status = $('#near-status');
  const pin = makeMarker('probe');
  const src = () => map.getSource('near-dots');
  const ring = () => map.getSource('near-ring');
  let ctl = null;
  map.on('load', () => {
    map.addSource('near-ring', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'near-ring', type: 'line', source: 'near-ring', paint: { 'line-color': '#8a5a00', 'line-width': 1.5, 'line-dasharray': [3, 2] } });
    map.addSource('near-dots', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'near-dots', type: 'circle', source: 'near-dots',
      paint: { 'circle-radius': 5, 'circle-color': ['match', ['get', 'layer'], 'address', '#8a5a00', 'venue', '#2e8b57', '#1f5f8b'],
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2 } });
  });
  function circleFeature(lat, lon, km) {
    const pts = [];
    for (let i = 0; i <= 64; i += 1) {
      const a = (i / 64) * 2 * Math.PI;
      pts.push([lon + (km / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a), lat + (km / 111.32) * Math.cos(a)]);
    }
    return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [pts] } };
  }
  const ORDER = ['address', 'street', 'venue', 'neighbourhood', 'locality', 'localadmin', 'postalcode', 'county'];
  const TITLE = { address: 'Address here', street: 'Streets', venue: 'Venues', neighbourhood: 'Neighbourhood',
    locality: 'Town', localadmin: 'Municipality', postalcode: 'ZIP', county: 'County' };

  map.on('click', async (e) => {
    if (current() !== 'tab-nearby') return;
    const { lat, lng: lon } = e.lngLat;
    const km = Number($('#near-radius').value);
    pin.setLngLat([lon, lat]).addTo(map);
    ring()?.setData(circleFeature(lat, lon, km));
    ctl?.abort(); ctl = new AbortController();
    status.textContent = `Looking within ${distanceLabel(km)}...`;
    out.replaceChildren();
    try {
      // Two calls: the single nearest address (what is *here*), then the neighbourhood of it.
      const [here, around] = await Promise.all([
        client.reverse(lat, lon, { size: 1, layers: ['address'], radiusKm: Math.min(km, 0.5) }, ctl.signal),
        client.reverse(lat, lon, { size: 40, radiusKm: km }, ctl.signal),
      ]);
      const feats = around.features || [];
      src()?.setData({ type: 'FeatureCollection', features: feats });
      const h = here.features?.[0];
      status.replaceChildren();
      if (h) {
        status.append(el('span', 'badge exact', 'here'), document.createTextNode(` ${h.properties.label} (${distanceLabel((h.properties.distance ?? 0))})`));
      } else {
        status.append(el('span', 'badge place', 'no address within 500 m'));
      }
      const groups = new Map();
      for (const f of feats) {
        const k = f.properties.layer; if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(f);
      }
      for (const k of [...ORDER, ...[...groups.keys()].filter((x) => !ORDER.includes(x))]) {
        const list = groups.get(k); if (!list) continue;
        const h3 = el('li', 'near-group', `${TITLE[k] || k} (${list.length})`);
        out.append(h3);
        for (const f of list.slice(0, 8)) {
          const p = f.properties;
          const li = el('li', 'near-row');
          li.append(el('span', 'near-d', distanceLabel(p.distance ?? 0)), el('span', 'r-label', p.name || p.label));
          li.addEventListener('click', () => map.flyTo({ center: f.geometry.coordinates, zoom: Math.max(map.getZoom(), 16), padding: panelPadding(), duration: 500 }));
          out.append(li);
        }
      }
      if (!feats.length) out.append(el('li', 'hint', 'Nothing within that radius.'));
    } catch (err) {
      if (err.name !== 'AbortError') status.textContent = err.message;
    }
  });
  onTab((id) => {
    if (id === 'engine-change') { out.replaceChildren(); status.textContent = ''; src()?.setData(EMPTY); return; }
    if (id !== 'tab-nearby') { pin.remove(); src()?.setData(EMPTY); ring()?.setData(EMPTY); }
  });
}

// ---- helpers -----------------------------------------------------------------------------------

function bounds(features) {
  const b = new maplibregl.LngLatBounds();
  for (const f of features) b.extend(f.geometry.coordinates);
  return b;
}
