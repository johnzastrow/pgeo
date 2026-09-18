what is the most modern or best choice for an open-source, self-hosted geocoding server using my own data for showing a map of an address or places on a website

For your stated use case—**self-hosted, open source, driven by your own authoritative address and place data, with web-map search/autocomplete**—my primary recommendation is **PostGIS as the source of truth plus a thin purpose-built geocoding/search API**. It is more modern, maintainable, and data-governable than deploying a full planet-scale OSM geocoder when your data is the product.

If you instead need a public-facing “search almost anywhere” experience combining OSM, OpenAddresses, administrative areas, and your proprietary POIs, choose **Pelias**. It is the best established full-stack open-source option for heterogeneous datasets and exposes an API already shaped for map search. Pelias is modular, uses Elasticsearch, and has supported importers for OSM, OpenAddresses, Who’s on First, GeoNames, polylines, and arbitrary CSV; its CSV importer accepts custom records with coordinates and names, plus optional address components and namespaced custom metadata.\[[github](https://github.com/pelias/pelias/blob/master/README.md)\]\[[github](https://github.com/pelias/csv-importer)\]

## Recommendation by scenario

| Scenario | Best choice | Why |
| --- | --- | --- |
| Your authoritative local addresses, parcels, facilities, trails, assets, and named places are the core data | **PostGIS + custom API** | Direct control over schema, updates, ranking, security, geometry, and search behavior; no duplicate search datastore required |
| Need a Google-like geocoder across OSM plus public/open address data plus your POIs | **Pelias** | Purpose-built multi-source geocoder with forward/reverse endpoints, autocomplete-style search, and custom-data ingestion \[[pelias](https://pelias.io/)\]\[[github](https://github.com/pelias/pelias/blob/master/README.md)\] |
| Primarily OSM address and place lookup, including reverse geocoding, with a mature PostgreSQL-centric system | **Nominatim** | Strong canonical OSM geocoder; suitable when OSM is the primary dataset rather than a supplementary layer \[[nominatim](https://nominatim.org/release-docs/develop/)\]\[[nominatim](https://nominatim.org/)\] |
| Fast, easy OSM place/autocomplete service from a prebuilt index | **Photon** | Good operationally simple OSM search; Photon 1.0 arrived in 2026 and can serve locally, but it is a weaker fit for deeply integrated proprietary data workflows \[[nominatim](https://nominatim.org/2026/02/11/photon-1.0-released.html)\]\[[github](https://github.com/komoot/photon)\] |
| Address-only search for a bounded, high-quality address dataset | **Addok** | Small, focused address-search engine with GeoJSON API, reverse and batch geocoding, and a Redis backend \[[github](https://github.com/addok/addok)\]\[[github](https://github.com/addok/addok/blob/master/docs/index.md)\] |
| US-only, primarily conventional street-address geocoding from Census data | **PostGIS TIGER geocoder** | Built-in PostGIS ecosystem option, but it is a specialized TIGER/Line geocoder—not a general place/POI search system \[[postgis](https://postgis.net/docs/Extras.html)\]\[[postgis](https://postgis.net/docs/Geocode.html)\] |

## Why PostGIS + API is likely best

“Geocoding” usually bundles together several different tasks:

*   **Forward geocoding:** `"42 Main St, Portland, ME"` → coordinate and normalized address
*   **Place search:** `"Eastern Prom"` or `"Fire Station 3"` → feature candidates
*   **Autocomplete:** `"easte"` → ranked suggestions as the user types
*   **Reverse geocoding:** coordinate → nearest/containing address, street, parcel, locality, or facility
*   **Map presentation:** pan/zoom to a point, bbox, line, or polygon

If your website is centered on _your_ addresses or named features, a general global geocoder creates needless indirection. You already have the data model, update process, access controls, and likely a PostGIS environment. Put search and ranking where the data lives.

A practical stack:

```
Authoritative data feeds / editing workflows
        ↓
PostgreSQL + PostGIS
  ├─ address points / structures
  ├─ street centerlines + ranges
  ├─ POIs / facilities / landmarks
  ├─ admin areas / neighborhoods
  ├─ parcels or service areas
  └─ a denormalized geocoder_search relation
        ↓
FastAPI (or PostgREST / pg_graphql)
        ↓
MapLibre GL JS / OpenLayers / Leaflet
```

For your engineering style, I would make the search relation a materialized view or a deliberately denormalized table—not a generic ORM abstraction—with:

*   Canonical display name and normalized address fields
*   An `aliases text[]` field for historical names, abbreviations, and common variants
*   `tsvector` for full-text search
*   `pg_trgm` indexes for prefix/typo-tolerant matching
*   `geometry(Point, 4326)` for result placement
*   Optional `bbox geometry` or a display geometry for polygons/lines
*   Explicit `feature_type`, `source`, `authority`, `updated_at`, and stable external IDs
*   A precomputed ranking score that can include feature class, prominence, recency, data authority, and map-distance bias

This supports much better domain behavior than a generic geocoder. For example, your local fire station should outrank an OSM café with a coincident name; a user-entered asset code can resolve to an internal facility; a neighborhood polygon can yield a map-fit bounding box rather than a meaningless centroid.

## A concrete PostGIS pattern

For a modest-to-large local dataset, I would use a unified search table along these lines:

```
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE geocoder.search_feature (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text NOT NULL,
    source_id       text NOT NULL,
    feature_type    text NOT NULL,
    name            text,
    address         text,
    city            text,
    region          text,
    postcode        text,
    country         text DEFAULT 'US',
    aliases         text[] NOT NULL DEFAULT '{}',
    geom            geometry(Geometry, 4326) NOT NULL,
    search_text     text GENERATED ALWAYS AS (
        lower(unaccent(concat_ws(' ',
            name, address, city, region, postcode,
            array_to_string(aliases, ' ')
        )))
    ) STORED,
    search_vector   tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', lower(unaccent(concat_ws(' ',
            name, address, city, region, postcode,
            array_to_string(aliases, ' ')
        ))))
    ) STORED,
    importance      real NOT NULL DEFAULT 0,
    UNIQUE (source, source_id)
);

CREATE INDEX search_feature_geom_gix
    ON geocoder.search_feature USING gist (geom);

CREATE INDEX search_feature_text_trgm_gix
    ON geocoder.search_feature USING gin (search_text gin_trgm_ops);

CREATE INDEX search_feature_tsv_gix
    ON geocoder.search_feature USING gin (search_vector);
```

Then an autocomplete endpoint should favor predictable prefix matching, limited result sets, and server-side ranking—not unrestricted fuzzy matching on every keystroke. A reasonable ordering formula is:

1.  Exact/prefix match on name or address
2.  Trigram similarity
3.  Full-text rank
4.  Feature importance
5.  Geographic proximity to the current map center, if supplied

Return GeoJSON feature collections so the browser stack stays simple:

```
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "facility:1234",
      "geometry": {
        "type": "Point",
        "coordinates": [-70.2553, 43.6591]
      },
      "properties": {
        "label": "Example Facility — 123 Example Street, Portland, ME",
        "feature_type": "facility",
        "score": 0.97
      }
    }
  ]
}
```

That is enough for MapLibre/OpenLayers to render a marker and `fitBounds` or `flyTo` directly.

## When Pelias is worth it

Pick Pelias if the user expectation is “search all local addresses, business names, neighborhoods, roads, and recognizable places,” while your owned data is only one source among OSM and other public datasets.

Its advantages:

*   A ready-made geocoder API rather than an API you must design.
*   Mature handling of heterogeneous sources and geographic hierarchies.
*   Elastic-backed candidate retrieval and ranking at broader scale.
*   Existing imports for major open geographic datasets and a CSV importer for proprietary data.
*   Custom sources and layers can be automatically discovered by the API at startup.\[[github](https://github.com/pelias/api)\]
*   The CSV import schema requires at least a `source`, latitude, longitude, and name; it can carry address components and custom JSON under `addendum`, which is useful for retaining application-specific attributes.\[[github](https://github.com/pelias/csv-importer)\]

Its trade-offs are architectural, not conceptual:

*   You own Elasticsearch operations, index lifecycle, memory tuning, imports, and reindexing.
*   Custom-data update workflows are less naturally transactional than your normal PostGIS edit/publish pipeline.
*   You may need a separate application-specific lookup API anyway for internal IDs, secured records, rich geometries, and bespoke relevance rules.
*   Pelias is especially compelling when wide-area coverage matters. It is generally overbuilt for a city, county, campus, utility territory, or curated place directory whose source records are already well managed in PostGIS.

A good hybrid is feasible: keep authoritative entities in PostGIS, publish a flattened search feed into Pelias for public broad search, and retain a first-party PostGIS endpoint for privileged or domain-specific lookup.

## Why not default to Nominatim or Photon

### Nominatim

Nominatim is excellent for OSM-first geocoding and reverse geocoding. It searches OSM names and addresses and provides limited feature-type search. Its import pipeline is mature and supports scoped import modes ranging from administrative-only through streets, addresses, and POIs.\[[nominatim](https://nominatim.org/release-docs/develop/)\]\[[nominatim](https://nominatim.org/release-docs/latest/admin/Import/)\]

But it should not be the default choice for a custom-data-centric application:

*   Its data and ranking model are fundamentally OSM-oriented.
*   Its formal path for custom datasets is not as clean as “load my PostGIS table and expose it as a first-class source.”
*   The documented `add-data` flow is mostly for OSM input and TIGER house-number data; after loading, it must still be indexed.\[[man.archlinux](https://man.archlinux.org/man/extra/nominatim/nominatim.1.en)\]\[[nominatim](https://nominatim.org/release-docs/latest/admin/Advanced-Installations/)\]
*   At planet scale, it is operationally substantial: current documentation estimates at least 64 GB RAM and roughly 900 GB SSD for a full global import. Regional extracts are much more reasonable, but the comparison illustrates the scope of the platform.\[[nominatim](https://nominatim.org/release-docs/latest/admin/Import/)\]

Use it when you want a self-hosted OSM geocoder, not when you want your organization’s database to _be_ the geocoder.

### Photon

Photon is a useful choice if you want a quick OSM place-search endpoint. It runs locally, is built for OSM data, imports OSM using Nominatim-related tooling, and offers downloadable ready-made indexes; its service commonly listens on port 2322.\[[github](https://github.com/komoot/photon)\]

For your scenario, its limitation is that it is mainly a convenient OSM-search distribution, not a flexible authoritative-data platform. Use it as an OSM supplement or a rapid prototype, not the core data service.

## Implementation choice

I would choose one of these two paths:

### Path A: Recommended for your own local data

**PostGIS + FastAPI + MapLibre GL JS**

*   Store and edit all authoritative features in PostGIS.
*   Build a `geocoder.search_feature` projection using SQL, dbt, or a Python ETL job.
*   Use `pg_trgm`, full-text search, and spatial indexes.
*   Expose `/search`, `/reverse`, and `/feature/{source}/{id}` endpoints.
*   Return GeoJSON and explicit confidence/match metadata.
*   Add a small OSM/GeoNames ingestion layer only for supplementary context.
*   Version your normalization and ranking logic with migrations and tests.

This gives you a lean service whose operational footprint matches a normal PostGIS application, with ranking rules that are transparent and testable.

### Path B: Recommended for broad public geocoding

**Pelias + Elasticsearch + OSM/OpenAddresses + custom CSV importer**

*   Deploy the official Docker-oriented Pelias stack for your target area.
*   Import OSM and whichever open address/admin sources matter.
*   Publish your internal locations as a normalized Pelias CSV source.
*   Treat Pelias as a search index, not your system of record.
*   Rebuild or incrementally refresh from your PostGIS export pipeline.
*   Put a lightweight gateway in front of Pelias for rate limits, API shaping, caching, tenant/security controls, and telemetry.

## Bottom line

For “show an address or place on a website using my own data,” build a **PostGIS-native geocoding/search service** first. It is the modern choice in the sense that it avoids unnecessary duplicated infrastructure, preserves authoritative data semantics, and gives you full control over autocomplete and relevance.

Use **Pelias** when the product requirement is truly a general-purpose, multi-source geocoder. Use **Nominatim** or **Photon** when OSM itself is the primary corpus. For an address-only bounded corpus, consider **Addok**, but PostGIS is usually the more strategic choice for you because it integrates naturally with the rest of your GIS and data infrastructure.

Follow up requirements: 

These are largely from Pelias, but I wonder if we can simplify the stack and still meet some of these requirements. The dream is to reproduce the Pelias capabilities and API but entirely inside of PostGIS, with user-facing aspects (web widget, data loading facilities, management controls, etc.) built in some external technologies.

1.  Web and Mobile Autocomplete, auto-complete search with single form field (linked to map on page), with fuzzy suggestions
2.  batch geocode, batch reverse geocode - to selectable levels.
3.  Many datatypes and sources to support geocoding and reverse geocoding
    1.  [**OpenStreetMap**](https://github.com/pelias/openstreetmap/): supports importing nodes, ways and relations from [OpenStreetMap](http://openstreetmap.org/)
    2.  [**OpenAddresses**](https://github.com/pelias/openaddresses/): supports importing the hundreds of millions of global addresses collected from various authoritative government sources by [OpenAddresses](https://openaddresses.io/)
    3.  [**Who's on First**](https://github.com/pelias/whosonfirst/): supports importing admin areas and venues from [Who's on First](https://www.whosonfirst.org/)
    4.  [**Geonames**](https://github.com/pelias/geonames/): supports importing admin records and venues from [Geonames](http://www.geonames.org/)
    5.  [**Polylines**](https://github.com/pelias/polylines): supports any data in the [Google Polyline format](https://developers.google.com/maps/documentation/utilities/polylinealgorithm?csw=1). It's mainly used to import roads from OpenStreetMap
    6.  [**CSV**](https://github.com/pelias/csv-importer): supports importing any data in CSV format, which is great for custom data or proprietary data
    7.  US GNIS, US Zipcode Tabulation Areas
    8.  Place data from Overture, other data from there if it makes sense
4.  Structured and Unstructured Search - address data, lat/lon, postal code, and city.
5.  Create a reusable Javascript Element
6.  Viewport Biasing - Prefer results located within a given viewport or bounding-box as configuration in the source app
7.  Region Biasing - Restrict results to a specific region, or multiple regions.
8.  Component Filtering - Filter results matching specific criteria such as source, layer or category. For example, prevent businesses from appearing
9.  Confidence Scores - Determine match quality with confidence scores on every result.
10.  Easy to get data loaded and updated