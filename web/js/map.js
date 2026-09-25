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
    // Pan limits match the basemap extract, so no untiled area is ever shown.
    maxBounds: region.bounds,
    attributionControl: false,
    cooperativeGestures: false,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial', maxWidth: 140 }), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution:
      'Geocoding: Pelias / pgeo &middot; OpenAddresses &middot; OpenStreetMap &middot; Who&#39;s On First &middot; USGS GNIS &middot; US Census &middot; Overture Maps',
  }), 'bottom-right');
  return map;
}

// Chart-style marker: a magenta light flare with a small ring, as for a lit aid.
export function makeMarker(kind = 'primary') {
  const el = document.createElement('div');
  el.className = `chart-mark chart-mark-${kind}`;
  const flare = document.createElement('span');
  flare.className = 'chart-mark-flare';
  const ring = document.createElement('span');
  ring.className = 'chart-mark-ring';
  el.append(flare, ring);
  return new maplibregl.Marker({ element: el, anchor: 'bottom' });
}

export { maplibregl };
