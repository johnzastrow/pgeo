// Basemap: MapLibre GL + self-hosted Protomaps PMTiles, tinted like a nautical chart.
// pmtiles.js and basemaps.js are classic scripts that define the globals `pmtiles` and
// `basemaps` (see index.html); MapLibre is imported as an ES module.
import * as maplibregl from '../vendor/maplibre-gl/maplibre-gl.mjs';

const ORIGIN = window.location.origin;

// The region this deployment covers. The server states it in /region.json (the edge role writes
// it from regions/regions.json); a server that predates that file is a Maine one, so those are
// the fallback values. The bounds double as pan limits, so no untiled area is ever shown.
export const DEFAULT_REGION = {
  build: 'me',
  name: 'Maine',
  bounds: [[-71.2, 42.9], [-66.8, 47.5]],
  tiles: '/tiles/maine.pmtiles',
};

export async function loadRegion() {
  try {
    const res = await fetch('/region.json', { headers: { Accept: 'application/json' } });
    if (!res.ok) return DEFAULT_REGION;
    const r = await res.json();
    const ok = Array.isArray(r?.bounds) && r.bounds.length === 2 && typeof r.tiles === 'string';
    return ok ? { build: r.build || '', name: r.name || '', bounds: r.bounds,
                  tiles: r.tiles, features: r.features || 0 }
              : DEFAULT_REGION;
  } catch {
    return DEFAULT_REGION;
  }
}

// Map flavor to match the page: a light neutral ground, cool grey-blue water, and the logo's
// blue for boundaries and labels of consequence. Everything is low-contrast on purpose - the
// markers and the result panel are what should carry colour, not the basemap.
function chartFlavor() {
  const base = window.basemaps.namedFlavor('light');
  return {
    ...base,
    background: '#e7eaee', earth: '#f2f4f6', water: '#cfdde8',
    park_a: '#e4ebe2', park_b: '#dae4d8', wood_a: '#e5ebe3', wood_b: '#dbe3d8',
    scrub_a: '#e8ece7', scrub_b: '#dfe4dd', sand: '#efeee7', beach: '#f0eee4',
    buildings: '#e2e6ea', pier: '#e2e6ea',
    other: '#ffffff', minor_service: '#ffffff', minor_a: '#ffffff', minor_b: '#ffffff',
    link: '#ffffff', major: '#ffffff', highway: '#f7f8fa',
    minor_service_casing: '#dde1e6', minor_casing: '#dde1e6', link_casing: '#d3d8de',
    major_casing_early: '#d3d8de', major_casing_late: '#d3d8de',
    highway_casing_early: '#c3cad2', highway_casing_late: '#c3cad2',
    railway: '#a8b0b9', boundaries: '#0264a4',
    roads_label_minor: '#76808d', roads_label_minor_halo: '#f2f4f6',
    roads_label_major: '#56606d', roads_label_major_halo: '#f2f4f6',
    city_label: '#16202c', city_label_halo: '#f2f4f6',
    subplace_label: '#44505f', subplace_label_halo: '#f2f4f6',
    state_label: '#8a939d', state_label_halo: '#f2f4f6',
    ocean_label: '#4a7fa5',
  };
}

// How far past the region the basemap extends, as a fraction of its span. Must match
// BASEMAP_MARGIN in scripts/fetch_data.sh: that script extracts the tiles, this decides how far
// the user may pan into them, and if this were the larger of the two the map would show blank.
const BASEMAP_MARGIN = 0.15;

function panBounds([[w, s], [e, n]]) {
  const dx = (e - w) * BASEMAP_MARGIN;
  const dy = (n - s) * BASEMAP_MARGIN;
  return [[w - dx, s - dy], [e + dx, n + dy]];
}

export function createMap(container, region = DEFAULT_REGION) {
  const protocol = new window.pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);

  const style = {
    version: 8,
    glyphs: `${ORIGIN}/vendor/glyphs/{fontstack}/{range}.pbf`,
    sprite: `${ORIGIN}/vendor/sprites/light`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${ORIGIN}${region.tiles}`,
        attribution:
          '<a href="https://protomaps.com">Protomaps</a> &copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
      },
    },
    layers: window.basemaps.layers('protomaps', chartFlavor(), { lang: 'en' }),
  };

  const map = new maplibregl.Map({
    container,
    style,
    bounds: region.bounds,
    fitBoundsOptions: { padding: 40 },
    // Pan limits are the basemap's extent, not the region's. The extract is taken with the same
    // margin (BASEMAP_MARGIN in scripts/fetch_data.sh), so this is still "no untiled area is ever
    // shown" - but clamping to the region's own box left no room to move: the cartouche floats
    // over the left of the map, and the part of the state beneath it could not be panned out.
    maxBounds: panBounds(region.bounds),
    attributionControl: false,
    cooperativeGestures: false,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial', maxWidth: 140 }), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    // Data sources only. It used to lead with "Geocoding: Pelias / pgeo", which named whichever
    // engines the *development* stack happened to run and was simply wrong on a deployment
    // serving one of them - and attribution is owed to whoever the data came from, not to the
    // software that queried it. Which engine answered is already stated in the panel, and it
    // changes with the engine; this line does not. See docs/DEMO_ENGINES.md.
    customAttribution:
      'Geocoding data: OpenAddresses &middot; OpenStreetMap &middot; Who&#39;s On First &middot; USGS GNIS &middot; US Census &middot; Overture Maps',
  }), 'bottom-right');

  // The cartouche is an overlay, so the map's usable area is not the whole canvas. Telling
  // MapLibre about the inset makes every camera move - the opening fit, flyTo on a result, the
  // "bias to map centre" reading - use the part the user can actually see. Without it the
  // opening view centres Maine on the full canvas and puts the south-west third behind the panel.
  const applyInset = () => {
    const el = document.querySelector('.cartouche');
    if (!el) return;
    const box = el.getBoundingClientRect();
    const wide = window.innerWidth > 720;   // matches the bottom-sheet breakpoint in app.css
    map.setPadding(wide
      ? { top: 24, right: 24, bottom: 24, left: Math.min(box.right + 24, window.innerWidth * 0.45) }
      : { top: 24, right: 24, left: 24, bottom: Math.min(box.height + 24, window.innerHeight * 0.5) });
  };
  map.once('load', () => { applyInset(); map.fitBounds(region.bounds, { padding: 16, animate: false }); });
  window.addEventListener('resize', applyInset);
  return map;
}

// Two roles, two shapes. `primary` is the answer the geocoder returned, so it is a pin: its tip
// sits on the coordinate and it reads unmistakably as an overlay rather than as basemap data.
// `probe` is a point the user supplied - a reverse-geocode click, an address point - so it is a
// dot centred on the coordinate, which hides nothing underneath it.
//
// What this replaced: a rotated teardrop "light flare" with a detached ring below it, left over
// from the chart-room styling. At a distance the two pieces read as a smudge rather than a
// position, and the flare pulsed, which drew the eye without saying anything.
const SVG = 'http://www.w3.org/2000/svg';

export function makeMarker(kind = 'primary') {
  const el = document.createElement('div');
  el.className = `mark mark-${kind}`;
  if (kind !== 'primary') {
    // The dot is drawn entirely in CSS: core, ring and halo are background and box-shadow.
    return new maplibregl.Marker({ element: el, anchor: 'center' });
  }
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 22 30');
  svg.setAttribute('width', '22');
  svg.setAttribute('height', '30');
  svg.setAttribute('aria-hidden', 'true');
  const body = document.createElementNS(SVG, 'path');
  // A circular head of radius 8 about (11,11), drawn down to a point at (11,29).
  body.setAttribute('d', 'M11 29C6 20.5 3 16.6 3 11a8 8 0 1 1 16 0c0 5.6-3 9.5-8 18Z');
  body.setAttribute('class', 'mark-body');
  const eye = document.createElementNS(SVG, 'circle');
  eye.setAttribute('cx', '11');
  eye.setAttribute('cy', '11');
  eye.setAttribute('r', '3.2');
  eye.setAttribute('class', 'mark-eye');
  svg.append(body, eye);
  el.append(svg);
  return new maplibregl.Marker({ element: el, anchor: 'bottom' });
}

export { maplibregl };
