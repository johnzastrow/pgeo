-- Tables for one build. The loader runs this with search_path = pgeo_build, public, so
-- unqualified names land in the build schema; 030_enrich_index.sql finishes the build and
-- the loader swaps pgeo_build -> pgeo atomically.

-- Admin areas (Who's On First; Overture divisions as an A/B source).
CREATE TABLE admin (
    id          bigint PRIMARY KEY,
    source      text NOT NULL,              -- whosonfirst | overture
    source_id   text NOT NULL,
    placetype   text NOT NULL,              -- region, county, localadmin, locality, neighbourhood, postalcode
    name        text NOT NULL,
    abbr        text,
    population  bigint,
    parent_id   bigint,
    geom        geometry(MultiPolygon, 4326),
    centroid    geometry(Point, 4326) NOT NULL,
    bbox        double precision[]          -- [minlon, minlat, maxlon, maxlat]
);

-- Raw rows as the loaders produce them (no hierarchy, no normalization).
CREATE TABLE feature_raw (
    rid           bigint GENERATED ALWAYS AS IDENTITY,
    source        text NOT NULL,
    layer         text NOT NULL,
    source_id     text NOT NULL,
    name          text NOT NULL,
    housenumber   text,
    street        text,
    unit          text,
    postcode      text,
    locality_hint text,                     -- town given by the source (OA city); used only if PIP finds none
    category      text[],
    addendum      jsonb,
    geom          geometry(Point, 4326) NOT NULL,
    bbox          double precision[],
    admin_id      bigint,
    popularity    real
);

-- Everything searchable, one row per result the API can return (built from feature_raw).
CREATE TABLE feature (
    id            bigint PRIMARY KEY,
    gid           text NOT NULL,            -- source:layer:source_id (Pelias format)
    source        text NOT NULL,
    layer         text NOT NULL,
    source_id     text NOT NULL,
    name          text NOT NULL,
    housenumber   text,
    street        text,
    unit          text,
    postcode      text,
    neighbourhood text,
    locality      text,
    localadmin    text,
    county        text,
    region        text,
    region_a      text,
    label         text,
    category      text[],
    addendum      jsonb,
    geom          geometry(Point, 4326) NOT NULL,
    bbox          double precision[],
    admin_id      bigint,                   -- admin rows that are also features
    hier          jsonb,                    -- Pelias hierarchy ids: locality_gid, county_gid, county_a, ...
    importance    real NOT NULL DEFAULT 0,
    -- normalized forms, filled by the enrich step
    name_norm     text,
    street_norm   text,
    locality_norm text,
    postal_locality_norm text,              -- town as the source wrote it (OA postal city)
    hn_int        integer,
    tokens        tsvector
);

-- Distinct street names per town: the small table fuzzy street matching runs against
-- before exact house-number lookups on the big one.
CREATE TABLE street_name (
    street_norm   text NOT NULL,
    locality_norm text NOT NULL,
    street        text NOT NULL,
    locality      text,
    n_addresses   integer NOT NULL,
    PRIMARY KEY (street_norm, locality_norm)
);

-- Top features per short prefix (1-3 characters) for autocomplete on very short input,
-- where a prefix scan would match tens of thousands of rows.
CREATE TABLE ac_prefix (
    prefix      text NOT NULL,
    rank        smallint NOT NULL,
    feature_id  bigint NOT NULL,
    PRIMARY KEY (prefix, rank)
);

-- feature_ac, the narrow table autocomplete reads, is created in 030_enrich_index.sql: it has to
-- be made with CREATE TABLE AS, for the reason given there.

-- Load metadata (sources, counts, timings) for reports and the API's /status.
CREATE TABLE build_info (
    key   text PRIMARY KEY,
    value jsonb NOT NULL
);

-- Staging (filled by the loaders with COPY; transformed by 022_stage.sql / 025_osm.sql).
CREATE UNLOGGED TABLE stage_admin (
    id bigint, source text, source_id text, placetype text, name text, abbr text,
    population bigint, parent_id bigint, geom_hex text,
    lon double precision, lat double precision,
    minlon double precision, minlat double precision, maxlon double precision, maxlat double precision
);
CREATE UNLOGGED TABLE stage_point (
    source text, layer text, source_id text, name text, housenumber text, street text,
    unit text, postcode text, locality_hint text, category text, addendum text,
    lon double precision, lat double precision, popularity real
);
