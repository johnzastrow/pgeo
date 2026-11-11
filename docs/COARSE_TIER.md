# The worldwide (coarse) tier

A build holds street-level data for its own states (for example Maine and New Hampshire). The
coarse tier adds a downsampled worldwide set of places, so a record outside the build still gets a
point: a town centroid, a ZIP centroid, a state or a country. It answers at a low precision and the
confidence says so.

## What is in it

| Layer | Source | What is kept |
|-------|--------|--------------|
| country | Overture divisions (country, dependency) | all |
| region | Overture divisions | all (4,000 worldwide, every US state) |
| county | Overture divisions | all (39,000) |
| localadmin | Overture divisions | all (21,500) |
| locality | Overture divisions | every US locality; elsewhere cities, towns and places of 1,000 people or more (about 350,000 in all) |
| postalcode | US Census ZCTA gazetteer | every ZIP centroid outside the build |

Overture is the only worldwide source: Natural Earth was rejected because its line and polygon
topology is not reliable enough. Places the build already covers in detail (its own states) are not
loaded again. Each coarse row is flagged `coarse` and carries its country (`country_code`, ISO
alpha-2, and `country`, the English name); `country_a` is the ISO alpha-3 code, looked up in
`geocode.country_ref` (the 249 ISO 3166-1 countries and Kosovo, XKX). Overture codes a few disputed
areas outside ISO 3166-1; they have no alpha-3, so `country_a` is left out for them. A worldwide
place's `region_a` is its ISO 3166-2 code ("ON", "03"); for US places it is the state abbreviation.

Licenses: Overture divisions derive from OpenStreetMap (ODbL 1.0) with some Esri Community Maps
records (CC BY 4.0); the ZCTA gazetteer is public domain. `/v1/attribution` says so.

## How it is fetched and built

```
scripts/fetch_data.sh coarse     # one shared file, all builds use it; part of "all"
PGEO_COARSE=0                    # at build time, leaves the tier out
```

The build adds the rows after the main feature insert, without the point-in-polygon step, so they
reach every downstream table (towns, autocomplete, street names) and the dump. A dump holds about 450,000
more rows than the same build without the tier.

## The `coarse` parameter

`coarse` is a request parameter on search, structured search, autocomplete and reverse. It defaults to
true. `coarse=false` leaves the worldwide tier out, so the build answers from its own data only:

- `/v1/search?text=Sacramento, CA` returns Sacramento, CA (a coarse locality).
- `/v1/search?text=Sacramento, CA&coarse=false` returns nothing: the build holds no California.
- `/v1/search?text=Portland, ME&coarse=false` is unchanged: Portland, ME is the build's own.

It differs from `layers=`, which filters by kind of place: `layers=address` would remove the build's
own towns as well. And it is not Pelias's `layers=coarse` alias, which means the admin layers (and now
includes `country`). Use `coarse=false` for strict in-region geocoding where no match is better than a
town-level guess, for quality checks, and to compare against results from before the tier.

Each coarse feature carries `"coarse": true` in its properties.

## How confidence stays honest

The engine scores how well an answer fits the question. A town asked for by name is a full answer
(1.0). A street address answered by its town is not: the query asked for a building, and the
engine returns the town at 0.45. The same holds outside the build. The batch tool goes one step further
for records, which are geocoded to find a place: it caps `pgeo_confidence` by the kind of place that
answered (street 0.6, ZIP 0.5, town 0.45, county 0.3, state 0.2, country 0.1) and says so in the Note
column.

Ranking never lets the worldwide tier push out the build's own answers:

- At equal confidence a local result comes first, and a worldwide town of the same name is not counted
  as a rival when deciding whether the answer is ambiguous ("Portland" is Portland, ME at full
  confidence, though Portland, OR exists).
- A query that names a state is answered from that state first. A confident answer, or a town-level
  one, inside the named state is returned as it is; only when the state has nothing is the search run
  across every state, with a result in another state (or in none, such as a worldwide place) cut to
  60% of its confidence. So "1 Main St, Springfield, IL" is Springfield, IL and not Main Street in
  Springfield, ME, and "20 Davis Drive, Belmont, CA" is Belmont, CA and not a Belmont, NH address.
- In autocomplete the build's own places come first: "Chi" is Chichester here before it is Chile.
  A prefix nothing local matches still finds the world ("Berl" finds Berlin, Germany after Berlin, NH).
- A country at the end of the query ("Oslo, Norway") limits the search to it. `boundary.country`
  takes an ISO alpha-2 or alpha-3 code; a three-letter code that is not an ISO alpha-3 code matches
  nothing.

## How the parser treats states and worldwide towns

Every US state and territory is known to the parser (`geocode.state_ref`), and the worldwide towns
are in the town list it reads, but neither is trusted as much as the build's own, because the
tokens MD, PA, MS and IN end names and credentials ("Eric Broen, MD") as often as they name a state,
and short words (Ter, Rd) are town names somewhere:

- The build's own states are recognised as they always were.
- A state the build does not hold is taken from a query only after a comma, with a known town before
  it: "Sacramento, CA", "Portland, Oregon". "Richard Batstone Md Pa" has no state.
- Worldwide towns count as the query's locality only once such a state has been recognised, and
  typos are corrected to the build's own towns only.
- A state name that is also one of the build's own towns (California, ME) is the state when a town
  comes before it.
- A country is taken from the query only as the last comma-separated part and not the whole query:
  "Oslo, Norway" is Oslo, in Norway, while "red hill, west peru" (West Peru is a Maine town) and
  "Norway" alone are not split.

## Reverse geocoding outside the region

The tier holds the land boundary of every worldwide region and country (Overture division areas,
simplified to about 1 km; `pgeo.coarse_area`). A reverse lookup returns:

1. the nearest coarse towns and counties within the radius (default 50 km), at the distance's
   confidence and never above 0.45, but only those in the country and state that contain the point,
   so a town across a border is not offered;
2. the region that contains the point, then the country, at confidence 0.7.

A point in open country with no town in range still gets its state and country. Because the shapes
are simplified, a point within about a kilometre of a border can land on the wrong side. Counties
have no boundaries in the tier, only centroids. `coarse=false` leaves all of this out;
`layers=region` or `layers=country` returns just that level.

## Limits

- Worldwide coverage is towns and larger. Hamlets and villages below 1,000 people are loaded only
  in the United States.
- Local-language names are searchable for countries, regions and counties (for example Deutschland);
  towns are searched by their English or primary name.
- Reverse geocoding outside the region returns the containing state and country, but a containing
  county or town only as the nearest centroid.
- A state outside the build typed without a comma is recognised only when the town before it is in
  that state ("Sacramento CA"). A town that is not in the tier, or misspelled, leaves the state in the
  text; use a comma.
- Worldwide places carry their region's ISO code but the parser knows US states only: "Toronto ON"
  is not split.

## What it cost, measured

On the 1,560 Maine accuracy cases with the same Maine and New Hampshire data: 94.9% before the
tier, 93.8% after. Ten of the 19 cases that changed are the "miss" group (Austin, TX; Mount Rainier;
Grand Canyon): queries for places outside Maine that the suite expected to find nothing, and that the
tier now answers correctly. The rest are 9 cases (about 0.6 point) in venue, lake and town names where
a worldwide place of similar name now competes. Latency is unchanged (median 99 ms, 95th percentile
226 ms). The database is about 25% larger (2.06 million features; 260 MB dump).

A reverse lookup used to ask the whole feature table for the nearest coarse town, which took four
seconds; the build now makes a spatial index over the coarse rows alone.
