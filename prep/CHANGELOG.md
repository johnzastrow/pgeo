# Changelog: pelias-prep

Data preparation tools for the Pelias Maine build. Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Version source:
`prep/pyproject.toml`. Tags: `prep-vX.Y.Z` (assigned retroactively on 2026-09-18).

## [Unreleased]

## [0.2.0] - 2026-09-22

### Added
- `--build`: a run covers one or more US states from `regions/regions.json`. Inputs come from
  `data/raw/<build>/` and `data/raw/shared/`, outputs go to `data/processed/<build>/csv/`.
- `common.load_region_zctas` builds the `zctas` and `zcta_prefixes` tables from the Census
  ZCTA-to-county relationship file; `common.load_region_polygon` replaces the Maine-only
  polygon loader.
- GNIS converts one file per state and filters each against that state's own box, so a
  neighbour's features cannot arrive through the union box on a multi-state build.

### Changed
- No converter names a state. The ZIP prefix range, the `maine` table, the Maine bounding box and
  the `DomesticNames_ME` filename are all gone; the Maine build reproduces its previous GNIS and
  Overture output byte for byte.

## [0.1.0] - 2026-09-18

### Added
- `gnis`: USGS GNIS names for Maine as Pelias CSV (out-of-state points removed).
- `zcta`: Census ZCTA centroids for Maine ZIP codes (pipe-delimited input).
- `overture`: Overture Maps places for Maine as Pelias CSV (state filter).
- `oa-interp`: OpenAddresses GeoJSON to the legacy CSV the interpolation builder needs.
