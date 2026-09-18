// Basemap: MapLibre GL + self-hosted Protomaps PMTiles, tinted like a nautical chart.
// pmtiles.js and basemaps.js are classic scripts that define the globals `pmtiles` and
// `basemaps` (see index.html); MapLibre is imported as an ES module.
import * as maplibregl from '../vendor/maplibre-gl/maplibre-gl.mjs';

const ORIGIN = window.location.origin;
export const MAINE_BOUNDS = [[-71.2, 42.9], [-66.8, 47.5]];

// "Chart paper" flavor: buff land, chart-blue water, magenta boundaries, ink labels.
function chartFlavor(dark) {
  const base = window.basemaps.namedFlavor(dark ? 'dark' : 'light');
  if (dark) {
    return {
      ...base,
      background: '#0d1624', earth: '#141d2c', water: '#0a2238',
      boundaries: '#8a3d63', city_label: '#e8dcc0', city_label_halo: '#0d1624',
      ocean_label: '#6f93bf', state_label: '#9a8f78',
    };
  }
  return {
    ...base,
    background: '#e9dfc3', earth: '#f3ead2', water: '#b7d0df',
    park_a: '#e4e5c8', park_b: '#d4dcb4', wood_a: '#e8e6cb', wood_b: '#dcdcb5',
    scrub_a: '#e9e5c9', scrub_b: '#dedbb8', sand: '#efe3c2', beach: '#f2e4bd',
    buildings: '#e3d7b9', pier: '#e3d7b9',
    other: '#fbf6e9', minor_service: '#fbf6e9', minor_a: '#fbf6e9', minor_b: '#fffaf0',
    link: '#fffaf0', major: '#fffaf0', highway: '#f7ecd2',
    minor_service_casing: '#dccfae', minor_casing: '#dccfae', link_casing: '#d3c39d',
    major_casing_early: '#d3c39d', major_casing_late: '#d3c39d',
    highway_casing_early: '#c7b186', highway_casing_late: '#c7b186',
    railway: '#9a8f78', boundaries: '#b3246b',
    roads_label_minor: '#6b6250', roads_label_minor_halo: '#f3ead2',
    roads_label_major: '#4f4636', roads_label_major_halo: '#f3ead2',
    city_label: '#14213d', city_label_halo: '#f3ead2',
    subplace_label: '#3d4a66', subplace_label_halo: '#f3ead2',
    state_label: '#8a7d64', state_label_halo: '#f3ead2',
    ocean_label: '#3f6592',
  };
}

export function createMap(container, { dark = false } = {}) {
  const protocol = new window.pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);

  const style = {
    version: 8,
    glyphs: `${ORIGIN}/vendor/glyphs/{fontstack}/{range}.pbf`,
    sprite: `${ORIGIN}/vendor/sprites/${dark ? 'dark' : 'light'}`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${ORIGIN}/tiles/maine.pmtiles`,
        attribution:
          '<a href="https://protomaps.com">Protomaps</a> &copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
      },
    },
    layers: window.basemaps.layers('protomaps', chartFlavor(dark), { lang: 'en' }),
  };

  const map = new maplibregl.Map({
    container,
    style,
    bounds: MAINE_BOUNDS,
    fitBoundsOptions: { padding: 40 },
    // Pan limits match the basemap extract, so no untiled area is ever shown.
    maxBounds: [[-71.2, 42.9], [-66.8, 47.5]],
    attributionControl: false,
    cooperativeGestures: false,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial', maxWidth: 140 }), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution:
      'Geocoding: Pelias &middot; OpenAddresses &middot; Who&#39;s On First &middot; USGS GNIS &middot; US Census &middot; Overture Maps',
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
