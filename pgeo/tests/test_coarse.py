"""The coarse tier's staging step (pgeo/src/pgeo/load/coarse.py), on a small synthetic extract.

No database and no network: a handful of Overture-shaped division rows and a three-line ZCTA
Gazetteer are written to a temporary directory, and the CSV that would feed stage_coarse is read
back. What is pinned is the contract the engine is coded against: labels, layers, alias rows,
what is left out because the build holds it in detail, importance bands and bounding boxes.
"""

# ruff: noqa: E501  (the fixture tables are rows of data)
from __future__ import annotations

import io
import zipfile

import duckdb
import pytest

from pgeo.load import coarse
from pgeo.regions import Region

MAINE = Region(build="me", states=("ME",), wof_ids=(1,), bbox=(-71.1, 43.0, -66.9, 47.5))

STATES = [("06", "CA", "California"), ("23", "ME", "Maine"), ("33", "NH", "New Hampshire")]

# id, subtype, class, country, region, primary, english, population, lon, lat, county_id, bbox
DIVISIONS = [
    ("us", "country", None, "US", None, "United States", "United States", 331_000_000, -100.0, 39.0, None, (-179.0, 18.0, 179.0, 71.0)),
    ("de", "country", None, "DE", None, "Deutschland", "Germany", 83_000_000, 10.4, 51.1, None, (5.8, 47.2, 15.0, 55.0)),
    ("no", "country", None, "NO", None, "Norge", "Norway", 5_000_000, 11.5, 64.5, None, (4.5, 57.9, 31.1, 71.1)),
    ("pr", "dependency", None, "PR", None, "Puerto Rico", "Puerto Rico", 3_000_000, -66.4, 18.2, None, (-67.3, 17.9, -65.2, 18.5)),
    ("us-ca", "region", None, "US", "US-CA", "California", "California", 39_000_000, -119.0, 37.0, None, (-124.4, 32.5, -114.1, 42.0)),
    ("us-me", "region", None, "US", "US-ME", "Maine", "Maine", 1_300_000, -69.0, 45.2, None, (-71.1, 43.0, -66.9, 47.5)),
    ("no-03", "region", None, "NO", "NO-03", "Oslo", "Oslo", 700_000, 10.7, 59.9, None, (10.5, 59.8, 10.9, 60.0)),
    ("c-sac", "county", None, "US", "US-CA", "Sacramento County", "Sacramento County", 1_500_000, -121.3, 38.5, None, (-121.9, 38.0, -120.9, 38.7)),
    ("c-cum", "county", None, "US", "US-ME", "Cumberland County", "Cumberland County", 300_000, -70.3, 43.8, None, (-70.9, 43.4, -69.9, 44.1)),
    ("l-sac", "locality", "city", "US", "US-CA", "Sacramento", "Sacramento", 490_000, -121.49, 38.58, "c-sac", None),
    ("l-por", "locality", "city", "US", "US-ME", "Portland", "Portland", 68_000, -70.25, 43.66, "c-cum", None),
    ("l-oslo", "locality", "city", "NO", "NO-03", "Oslo", "Oslo", 700_000, 10.74, 59.91, None, None),
    ("l-ber", "locality", "city", "DE", "DE-BE", "Berlin", "Berlin", 3_700_000, 13.4, 52.5, None, None),
    ("r-de", "region", None, "DE", "DE-BE", "Berlin", "Berlin", 3_700_000, 13.4, 52.5, None, (13.0, 52.3, 13.8, 52.7)),
    ("l-hamlet", "locality", "hamlet", "US", "US-CA", "Tinyville", "Tinyville", None, -120.0, 39.0, "c-sac", None),
    ("l-noname", "locality", "village", "NO", "NO-03", "Nameless", None, 800, 10.8, 59.95, None, None),
]

GAZ = (
    "GEOID|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG\n"
    "95864|1|1|6.28|0.231|38.584047|-121.375846\n"
    "04101|1|1|2.0|0.1|43.66|-70.26\n"
    "99999|1|1|3.0|0.2|10.0|20.0\n"
)
# a ZCTA belongs to the state holding most of its land
REL = (
    "GEOID_ZCTA5_20|GEOID_COUNTY_20|AREALAND_PART\n"
    "95864|06067|6000\n"
    "04101|23005|2000\n"
)


@pytest.fixture()
def divisions_file(tmp_path):
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE t (id VARCHAR, subtype VARCHAR, class VARCHAR, country VARCHAR, region VARCHAR, "
        "name_primary VARCHAR, name_en VARCHAR, population INTEGER, lon DOUBLE, lat DOUBLE, "
        "county_id VARCHAR, xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"
    )
    for r in DIVISIONS:
        bb = r[11] or (None, None, None, None)
        con.execute("INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [*r[:11], *bb])
    path = tmp_path / "divisions_test.parquet"
    con.execute("COPY t TO ? (FORMAT parquet)", [str(path)])
    return path


@pytest.fixture()
def zcta_dir(tmp_path, monkeypatch):
    d = tmp_path / "zcta"
    d.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2025_Gaz_zcta_national.txt", GAZ)
    (d / "2025_Gaz_zcta_national.zip").write_bytes(buf.getvalue())
    (d / "tab20_zcta520_county20_natl.txt").write_text(REL)
    monkeypatch.setattr(coarse, "ZCTA_DIR", d)
    return d


def _rows(tmp_path, divisions):
    out = tmp_path / "coarse.csv"
    n, by_layer, note = coarse.build_csv(out, MAINE, STATES, tmp_path, divisions=divisions)
    con = duckdb.connect()
    cols = [c[0] for c in con.execute("DESCRIBE SELECT * FROM read_csv(?)", [str(out)]).fetchall()]
    assert ", ".join(cols) == coarse.STAGE_COARSE_COLS
    rows = con.execute("SELECT * FROM read_csv(?, types={'postcode': 'VARCHAR'})", [str(out)]).fetchall()
    return n, by_layer, note, [dict(zip(cols, r, strict=True)) for r in rows]


def _one(rows, **where):
    hit = [r for r in rows if all(r[k] == v for k, v in where.items())]
    assert len(hit) == 1, (where, hit)
    return hit[0]


def test_the_builds_own_state_is_left_out(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    assert not [r for r in rows if r["region_a"] == "ME"]
    assert not [r for r in rows if r["name"] in ("Maine", "Cumberland County", "Portland")]
    assert not [r for r in rows if r["source_id"] == "04101"]  # a Maine ZIP is loaded in detail


def test_us_labels_use_the_state_abbreviation(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    assert _one(rows, name="Sacramento")["label"] == "Sacramento, CA, USA"
    assert _one(rows, name="Sacramento County")["label"] == "Sacramento County, CA, USA"
    assert _one(rows, name="California")["label"] == "California, USA"
    assert _one(rows, name="United States")["label"] == "United States"
    sac = _one(rows, name="Sacramento")
    assert (sac["region"], sac["region_a"], sac["county"], sac["locality"]) == (
        "California", "CA", "Sacramento County", "Sacramento")
    assert (sac["country_a"], sac["country"]) == ("US", "United States")


def test_non_us_labels_name_the_parents_and_drop_a_repeated_region(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    oslo = _one(rows, layer="locality", name="Oslo")
    assert oslo["label"] == "Oslo, Norway"  # region "Oslo" equals the locality, so it is dropped
    assert oslo["region_a"] is None and oslo["country_a"] == "NO"
    assert _one(rows, layer="locality", name="Berlin")["label"] == "Berlin, Germany"
    assert _one(rows, layer="region", name="Oslo")["label"] == "Oslo, Norway"
    assert _one(rows, layer="country", name="Norway")["label"] == "Norway"


def test_a_country_gets_an_alias_row_under_its_local_name(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    main = _one(rows, layer="country", name="Germany")
    alias = _one(rows, layer="country", name="Deutschland")
    assert alias["source_id"] == main["source_id"] + "#alt"
    assert alias["label"] == main["label"] == "Germany"
    assert main["addendum"] == '{"local_name":"Deutschland"}'
    # no alias where the names agree
    assert len([r for r in rows if r["name"] == "California"]) == 1
    # layer values: a dependency is a country
    assert _one(rows, name="Puerto Rico")["layer"] == "country"


def test_bounding_boxes_only_on_areas_and_importance_bands(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    ger = _one(rows, layer="country", name="Germany")
    assert (ger["minlon"], ger["minlat"], ger["maxlon"], ger["maxlat"]) == (5.8, 47.2, 15.0, 55.0)
    assert all(r["minlon"] is None for r in rows if r["layer"] in ("locality", "postalcode"))
    by = {r["layer"]: r["importance"] for r in rows if r["name"] in
          ("Germany", "California", "Sacramento County", "95864")}
    assert by["country"] == pytest.approx(0.9)
    assert by["region"] == pytest.approx(0.9)
    assert by["county"] == pytest.approx(0.4)
    assert by["postalcode"] == pytest.approx(0.3)
    # a locality: 0.35 plus a capped population boost, and just 0.35 with no population
    assert _one(rows, name="Sacramento")["importance"] == pytest.approx(0.5)
    assert _one(rows, name="Tinyville")["importance"] == pytest.approx(0.35)
    assert _one(rows, name="Nameless")["importance"] == pytest.approx(0.35 + 0.03 * 0.5877867, abs=1e-4)


def test_zips_outside_the_build_carry_their_state(tmp_path, divisions_file, zcta_dir):
    _, by_layer, note, rows = _rows(tmp_path, divisions_file)
    zip95 = _one(rows, layer="postalcode", source_id="95864")
    assert (zip95["source"], zip95["name"], zip95["postcode"]) == ("zcta", "95864", "95864")
    assert (zip95["label"], zip95["region_a"], zip95["country_a"]) == ("95864, CA, USA", "CA", "US")
    # no state known for 99999 (not in the relationship file): kept, labelled with the country only
    assert _one(rows, layer="postalcode", source_id="99999")["label"] == "99999, USA"
    assert by_layer["postalcode"] == 2 and note is None


def test_a_missing_zip_input_is_reported_not_fatal(tmp_path, divisions_file, monkeypatch):
    monkeypatch.setattr(coarse, "ZCTA_DIR", tmp_path / "nowhere")
    n, by_layer, note, _ = _rows(tmp_path, divisions_file)
    assert n > 0 and "postalcode" not in by_layer
    assert note and "ZIP" in note


def test_no_divisions_file_means_the_tier_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(coarse, "COARSE_DIR", tmp_path / "nowhere")
    n, by_layer, note = coarse.build_csv(tmp_path / "x.csv", MAINE, STATES, tmp_path)
    assert (n, by_layer) == (0, {}) and "no divisions file" in note
    assert not (tmp_path / "x.csv").exists()


def test_a_worldwide_place_carries_its_iso_region_code_in_hier(tmp_path, divisions_file, zcta_dir):
    *_, rows = _rows(tmp_path, divisions_file)
    # region_a stays US-only (the parser and the state filters read it as a US state); the short
    # code of a non-US region travels in hier and reaches the output as region_a
    assert _one(rows, layer="locality", name="Oslo")["hier"] == '{"region_a":"03"}'
    assert _one(rows, layer="locality", name="Berlin")["hier"] == '{"region_a":"BE"}'
    assert _one(rows, layer="region", name="Oslo")["hier"] == '{"region_a":"03"}'
    assert _one(rows, layer="locality", name="Sacramento")["hier"] is None
    assert _one(rows, layer="country", name="Norway")["hier"] is None


def test_boundaries_are_staged_as_hex_wkb_with_dependencies_as_countries(tmp_path):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(
        "CREATE TABLE a AS SELECT * FROM (VALUES ('us-ca', 'region', 'US', 'US-CA', "
        "ST_AsWKB(ST_GeomFromText('POLYGON((0 0,0 1,1 1,1 0,0 0))'))), "
        "('pr', 'dependency', 'PR', NULL, ST_AsWKB(ST_GeomFromText('POLYGON((5 5,5 6,6 6,6 5,5 5))')))) "
        "t(division_id, subtype, country, region, wkb)"
    )
    src = tmp_path / "areas_test.parquet"
    con.execute("COPY a TO ? (FORMAT parquet)", [str(src)])
    out = tmp_path / "areas.csv"
    assert coarse.build_areas_csv(out, src) == 2
    rows = con.execute("SELECT source_id, layer, wkb_hex FROM read_csv(?) ORDER BY 1", [str(out)]).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("pr", "country"), ("us-ca", "region")]
    assert all(r[2].startswith("01030000") for r in rows)  # WKB polygon, little-endian


def test_no_areas_file_means_no_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(coarse, "COARSE_DIR", tmp_path / "nowhere")
    assert coarse.build_areas_csv(tmp_path / "x.csv") == 0
    assert not (tmp_path / "x.csv").exists()
