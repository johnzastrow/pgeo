# Changelog: pelias-prep

Data preparation tools for the Pelias Maine build. Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Version source:
`prep/pyproject.toml`. Tags: `prep-vX.Y.Z` (assigned retroactively on 2026-09-18).

## [Unreleased]

## [0.1.0] - 2026-09-18

### Added
- `gnis`: USGS GNIS names for Maine as Pelias CSV (out-of-state points removed).
- `zcta`: Census ZCTA centroids for Maine ZIP codes (pipe-delimited input).
- `overture`: Overture Maps places for Maine as Pelias CSV (state filter).
- `oa-interp`: OpenAddresses GeoJSON to the legacy CSV the interpolation builder needs.
