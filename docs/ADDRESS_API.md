# Structured Address API (pgeo extension)

`GET /v1/address` returns a US address split into **USPS Publication 28** components, with
the standard abbreviations, the delivery and last lines, and the place context (municipality,
county, FIPS codes). It is a pgeo extension, not part of the Pelias API, and is served by
both pgeo front ends: FastAPI (`:4500`) and the pure-SQL API through PostgREST (`:4700`
edge). The logic is one SQL function, `geocode_api.v1_address` (`pgeo/sql/060_address.sql`).

Intended consumer: LANCER's `location` record (address, area, latitude, longitude).

## Inputs (exactly one)

| Input | Example | What comes back |
|-------|---------|-----------------|
| `ids` | `ids=openaddresses:address:c0ba2cc7f2551a58` (1 to 10 gids from search or autocomplete results) | The selected features, in order |
| `text` | `text=389 Congress St Apt 2, Portland ME` | The best search match (same ranking as `/v1/search`) |
| `point.lat` + `point.lon` | `point.lat=43.6568&point.lon=-70.2626` | The nearest address point within `radius`; if none, the town it is in |

Optional: `radius` (km, default 0.5, maximum 5) for nearest-address lookups; `unit`
(for example `Apt 2`, up to 20 characters) to add a secondary unit. In `text` mode a unit
typed in the text ("Apt 2", "Suite 200", "#3") is kept.

Invalid input returns HTTP 400 with a message (both front ends).

## What each kind of result contains

| Selected feature | `usps` block | `place` block |
|------------------|--------------|---------------|
| Address point | Its own address, `match: exact` | Yes |
| Venue or street (for example "Just in Time, Lewiston Maine") | The **nearest address point**, `match: nearest`, with `distance_m` | Yes |
| Town, county, ZIP, lake (admin areas and large features) | None: a centroid has no meaningful street address | Yes |
| Coordinate | Nearest address point (`match: nearest`, `distance_m`), else none | Yes (the town) |

This covers the requirement in `additions.md`: when a search result is not an address, the
API finds the nearest address point to the place and returns that street address, flagged
as `nearest` with its distance, so the caller can decide whether it is close enough.

## Response

```json
{
  "geocoding": { "version": "0.2", "query": {"text": "..."}, "engine": {"name": "pgeo-sql", "version": "0.6.0"},
                 "standard": "USPS Publication 28 style; not CASS-certified; ZIP+4 not available" },
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-70.2572641, 43.6592539]},
    "properties": {
      "gid": "openaddresses:address:c0ba2cc7f2551a58",
      "label": "389 Congress St, Portland, ME, USA",
      "confidence": 0.85,
      "usps": {
        "match": "exact",
        "primary_number": "389", "predirectional": null, "street_name": "CONGRESS",
        "suffix": "ST", "post_modifier": null, "postdirectional": null,
        "secondary_designator": "APT", "secondary_number": "2",
        "city": "PORTLAND", "state": "ME", "zip5": "04101",
        "delivery_line": "389 CONGRESS ST APT 2",
        "last_line": "PORTLAND ME 04101",
        "source": "openaddresses", "gid": "openaddresses:address:c0ba2cc7f2551a58"
      },
      "place": {
        "name": "389 Congress St", "layer": "address", "municipality": "Portland",
        "county": "Cumberland", "county_fips": "23005", "state": "Maine",
        "state_code": "ME", "state_fips": "23", "zip5": "04101",
        "lat": 43.659254, "lon": -70.257264
      }
    }
  }]
}
```

Null fields are omitted in the actual response.

Mapping to LANCER `location`: `address` = `delivery_line` + ", " + `last_line`;
`area` = `usps.city` (or `place.municipality`); `latitude`/`longitude` = the feature
geometry (for a `nearest` match, the place itself, not the address point).

## Standardization rules

- Uppercase; punctuation removed; C1 suffixes (`STREET` -> `ST`), C2 unit designators
  (`SUITE` -> `STE`; unknown designators become `#`), directionals (`NORTH` -> `N`).
- A leading direction is a pre-directional only when a name follows ("North Main St" ->
  `N MAIN ST`); a direction that is the name stays spelled out ("North St" -> `NORTH ST`).
- Post-directionals after a suffix, a route number or a one-word name ("Park Ave W",
  "US Route 2 W", "Parkway N").
- Numbered routes keep their words, and the number is not a suffix ("US Route 1" ->
  `US ROUTE 1`); "Main Street Extension" -> `MAIN ST EXT`.
- City: the postal city from the source when it has one, else the municipality.
- Coverage on Maine's data: 97.1% of the 41,868 distinct street names get a suffix; the
  rest have none in Publication 28 terms (Broadway, Rue Principale, Chandlers Wharf).

## Limits

- **Not CASS-certified and not a deliverability check**: it standardizes what the open data
  says. It cannot tell whether USPS delivers to the address.
- **No ZIP+4**: not in open data, and the licensed USPS file was declined (see "ZIP+4" below).
- **Last-line city** follows the source data. Where OpenAddresses gives the town ("PARIS")
  and USPS prefers another name for the ZIP ("SOUTH PARIS" for 04281), the USPS preference
  is not known without the USPS City State file.
- Units come only from the caller: search collapses a building's per-unit address records
  into one, so a record's own unit would be arbitrary.

## ZIP+4 and USPS reference data

**Decision (2026-09-19): ZIP+4 is dropped.** The API stays on open data; the research below
is kept for reference if the question comes back.

USPS sells the data that would close the last three gaps (checked 2026-09-19):

| Item | Fact | Source |
|------|------|--------|
| ZIP+4 Product | ~30 million address-range records nationally, monthly via Electronic Product Fulfillment; no software included | [USPS PostalPro: ZIP+4 Product](https://postalpro.usps.com/address-quality-solutions/zip-4-product) |
| Price | $120 per state or $1,750 all states (the City State file is included at no charge) | [AIS Products Pricing, July 2026](https://postalpro.usps.com/address-quality/AIS_Products_Pricing) |
| Licence | "for your internal corporate or personal use on one computer at one location" (a multi-user system counts as one); no transfer over a network or distribution without a paid Licence Amendment; data older than 105 days is not authorized; one-year term | [AIS Copyright/License Agreement](https://postalpro.usps.com/AISCopyright_License) |
| Amendment | Additional copies are priced by quantity; unlimited AIS licence $24,000 | same document |

What it would add: ZIP+4 codes, USPS-standard street spellings, the USPS preferred city for
each ZIP, and range validation (the house number falls in a range USPS knows; still not
full deliverability, which needs the separate DPV product). Whether serving results from it
to LANCER users counts as "internal corporate use" is a licence question for USPS, not a
technical one.

## Privacy

Queries to this endpoint carry street addresses, which LANCER treats as sensitive.
PostgreSQL is configured never to log bind parameters (`log_parameter_max_length = 0`, also
on error). Before production use with LANCER: disable query-string access logging at the
HTTP edge (or strip `text`, `ids`, `unit` and `point.*`), and keep the API LAN-only or behind
the Phase 11 authorization.
