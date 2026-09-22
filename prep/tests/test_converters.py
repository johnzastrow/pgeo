"""Converter tests on tiny synthetic inputs (no network, no real data)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pelias_prep import gnis, overture, zcta
from pelias_prep.common import connect, load_polygon_wkt, load_region_zctas, region

GNIS_HEADER = (
    "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|county_numeric|"
    "map_name|date_created|date_edited|bgn_type|bgn_authority|bgn_date|prim_lat_dms|"
    "prim_long_dms|prim_lat_dec|prim_long_dec|source_lat_dms|source_long_dms|source_lat_dec|"
    "source_long_dec"
)

REL_HEADER = (
    "OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|"
    "MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|FUNCSTAT_ZCTA5_20|OID_COUNTY_20|GEOID_COUNTY_20|"
    "NAMELSAD_COUNTY_20|AREALAND_COUNTY_20|AREAWATER_COUNTY_20|MTFCC_COUNTY_20|CLASSFP_COUNTY_20|"
    "FUNCSTAT_COUNTY_20|AREALAND_PART|AREAWATER_PART"
)

GAZ_HEADER = "GEOID|GEOIDFQ|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG"


def gnis_row(fid: str, name: str, cls: str, lat: str, lon: str) -> str:
    # 21 fields: 9 leading, 6 empty (date_edited .. prim_long_dms), lat, lon, 4 empty.
    empty6, empty4 = "|" * 6, "|" * 4
    return f"{fid}|{name}|{cls}|Maine|23|Piscataquis|021|Map|01/01/1980{empty6}|{lat}|{lon}{empty4}"


def rel_row(zcta5: str, county: str, land: int) -> str:
    """One ZCTA-to-county row: only the geoid, county geoid and land-area columns are read."""
    fields = [""] * 18
    fields[1] = zcta5  # GEOID_ZCTA5_20
    fields[9] = county  # GEOID_COUNTY_20
    fields[16] = str(land)  # AREALAND_PART
    return "|".join(fields)


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def reg():
    return region("me")


@pytest.fixture
def con():
    c = connect()
    # A box roughly covering Maine stands in for the Census polygon.
    load_polygon_wkt(c, "POLYGON((-71.1 43.0, -66.9 43.0, -66.9 47.4, -71.1 47.4, -71.1 43.0))",
                     "ME")
    # The two tables the real run derives from the Census relationship file.
    c.execute(
        "CREATE OR REPLACE TABLE zctas AS SELECT * FROM (VALUES "
        "('04101', 43.66, -70.26, 2.0, 0.5), ('03901', 43.29, -70.84, 37.5, 0.3)"
        ") t(geoid, lat, lon, aland_sqmi, awater_sqmi)"
    )
    c.execute("CREATE OR REPLACE TABLE zcta_prefixes AS "
              "SELECT * FROM (VALUES ('041'), ('039')) t(p)")
    return c


def test_region_resolves_a_named_build_and_a_bare_state_list():
    assert region("me").states == ("ME",)
    assert region("ny").states == ("NY",)
    multi = region("me,nh,vt")
    assert multi.states == ("ME", "NH", "VT")
    # The union box has to contain each member's box.
    assert multi.bbox[0] <= region("me").bbox[0]
    assert multi.bbox[2] >= region("me").bbox[2]
    with pytest.raises(ValueError):
        region("zz")


def test_zctas_go_to_the_state_holding_most_of_their_land(con, tmp_path, reg):
    """The rule that keeps Maine's islands and rejects a New Hampshire ZCTA.

    Deciding by geometry against the cartographic state polygon drops Peaks Island and the
    Cranberry Isles and admits 03579; deciding by land area, as the Census records it, does not.
    """
    gaz = tmp_path / "gaz.txt"
    gaz.write_text(
        "\n".join([
            GAZ_HEADER,
            "04108|x|1|1|0.4|0.9|43.66|-70.20",  # Peaks Island, all Maine
            "03579|x|1|1|560.0|3.0|44.93|-71.05",  # split, most land in Oxford County ME
            "03570|x|1|1|60.0|1.0|44.47|-71.19",  # Berlin NH, shares the 035 prefix
            "03801|x|1|1|9.0|1.0|43.07|-70.80",  # Portsmouth NH
        ]) + "\n"
    )
    rel = tmp_path / "rel.txt"
    rel.write_text(
        "\n".join([
            REL_HEADER,
            rel_row("04108", "23005", 1_000_000),          # Cumberland County, ME
            rel_row("03579", "23017", 846_017_376),        # Oxford County, ME - the majority
            rel_row("03579", "33007", 610_782_331),        # Coos County, NH
            rel_row("03570", "33007", 156_000_000),        # Coos County, NH - all of it
            rel_row("03801", "33015", 23_000_000),         # Rockingham County, NH
        ]) + "\n"
    )
    n = load_region_zctas(con, gaz, rel, reg)
    got = {r[0] for r in con.execute("SELECT geoid FROM zctas").fetchall()}
    assert got == {"04108", "03579"}
    assert n == 2
    # 035 is shared with New Hampshire, so it must not become an accepted prefix; 041 is not.
    prefixes = {r[0] for r in con.execute("SELECT p FROM zcta_prefixes").fetchall()}
    assert "041" in prefixes
    assert "035" not in prefixes
    assert "038" not in prefixes


def test_gnis_maps_fields_and_drops_out_of_area_and_historical(con, tmp_path, reg):
    src = tmp_path / "gnis.txt"
    src.write_text(
        "\n".join(
            [
                GNIS_HEADER,
                gnis_row("1", "Moosehead Lake", "Lake", "45.6", "-69.6"),
                gnis_row("2", "Katahdin", "Summit", "45.9", "-68.9"),
                gnis_row("3", "Old Mill (historical)", "Locale", "45.0", "-69.0"),
                gnis_row("4", "Atlantic Ocean", "Sea", "35.2", "-75.5"),
            ]
        )
        + "\n"
    )
    res = gnis.convert(con, [("ME", src)], tmp_path / "out.csv", reg)
    rows = read_rows(res.path)
    assert res.rows == 2
    assert [r["name"] for r in rows] == ["Moosehead Lake", "Katahdin"]
    assert rows[0]["source"] == "gnis"
    assert rows[0]["layer"] == "venue"
    assert rows[0]["category"] == "lake"
    assert json.loads(rows[0]["addendum_json_gnis"])["county"] == "Piscataquis"


def test_gnis_filters_each_state_against_its_own_box(con, tmp_path):
    """On a multi-state build, a feature is kept only by the file it came in."""
    two = region("me,nh")
    me_src, nh_src = tmp_path / "me.txt", tmp_path / "nh.txt"
    # Concord NH sits inside the Maine-plus-New-Hampshire union box but outside Maine's own.
    me_src.write_text(GNIS_HEADER + "\n" + gnis_row("1", "Concord", "Civil", "43.2", "-71.54")
                      + "\n" + gnis_row("2", "Katahdin", "Summit", "45.9", "-68.9") + "\n")
    nh_src.write_text(GNIS_HEADER + "\n" + gnis_row("3", "Concord", "Civil", "43.2", "-71.54")
                      + "\n")
    res = gnis.convert(con, [("ME", me_src), ("NH", nh_src)], tmp_path / "out.csv", two)
    rows = read_rows(res.path)
    assert [r["id"] for r in rows] == ["2", "3"]


def test_gnis_can_keep_historical(con, tmp_path, reg):
    src = tmp_path / "gnis.txt"
    src.write_text(
        GNIS_HEADER
        + "\n"
        + gnis_row("3", "Old Mill (historical)", "Locale", "45.0", "-69.0")
        + "\n"
    )
    res = gnis.convert(con, [("ME", src)], tmp_path / "out.csv", reg, include_historical=True)
    assert res.rows == 1


def test_zcta_exports_the_region_table(con, tmp_path, reg):
    res = zcta.convert(con, tmp_path / "out.csv", reg)
    rows = read_rows(res.path)
    assert [r["postcode"] for r in rows] == ["03901", "04101"]
    assert all(r["layer"] == "postalcode" and r["source"] == "zcta" for r in rows)


def write_overture_parquet(con, path: Path) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE ov AS
        SELECT * FROM (VALUES
            ('a', -70.2553, 43.6591, 0.9, 'open', 'Good Cafe', '123 Main St', '04101', 'ME'),
            ('b', -70.2553, 43.6591, 0.2, 'open', 'Low Confidence', '1 Elm St', '04101', 'ME'),
            ('c', -70.2553, 43.6591, 0.9, 'closed', 'Closed Shop', '2 Elm St', '04101', 'ME'),
            ('d', -72.5000, 44.0000, 0.9, 'open', 'Vermont Place', '3 Oak St', '05001', 'VT'),
            ('e', -70.2553, 43.6591, 0.9, NULL, 'PO Box Place', 'PO Box 9', '99999', 'ME')
        ) t(id, lon, lat, confidence, operating_status, name, freeform, postcode, region)
        """
    )
    con.execute(
        """
        COPY (
            SELECT
                id,
                ST_Point(lon, lat) AS geometry,
                {'primary': 'cafe', 'alternate': ['coffee_shop']} AS categories,
                confidence,
                ['https://example.org'] AS websites,
                ['+12075550100'] AS phones,
                {'names': {'primary': NULL}} AS brand,
                [{'freeform': freeform, 'locality': 'Portland', 'postcode': postcode,
                  'region': region, 'country': 'US'}] AS addresses,
                {'primary': name} AS names,
                [{'dataset': 'meta'}] AS sources,
                operating_status,
                'eat_and_drink' AS basic_category,
                {'primary': 'cafe', 'hierarchy': ['food_and_drink', 'cafe']} AS taxonomy,
                {'xmin': lon, 'xmax': lon, 'ymin': lat, 'ymax': lat} AS bbox
            FROM ov
        ) TO ? (FORMAT parquet)
        """,
        [str(path)],
    )


def test_overture_filters_and_parses_address(con, tmp_path, reg):
    src = tmp_path / "places.parquet"
    write_overture_parquet(con, src)
    res = overture.convert(con, src, tmp_path / "out.csv", reg)
    rows = {r["id"]: r for r in read_rows(res.path)}
    # b: low confidence, c: closed, d: outside Maine -> dropped
    assert set(rows) == {"a", "e"}
    assert rows["a"]["housenumber"] == "123"
    assert rows["a"]["street"] == "Main St"
    assert rows["a"]["postcode"] == "04101"
    assert rows["a"]["category"] == "eat_and_drink"
    # No leading number -> no street/housenumber; an out-of-region ZIP is dropped, not guessed.
    assert rows["e"]["housenumber"] == ""
    assert rows["e"]["street"] == ""
    assert rows["e"]["postcode"] == ""
    addendum = json.loads(rows["a"]["addendum_json_overture"])
    assert addendum["taxonomy"] == ["food_and_drink", "cafe"]


def test_overture_rejects_bad_parameters(con, tmp_path, reg):
    with pytest.raises(ValueError):
        overture.convert(con, tmp_path / "x.parquet", tmp_path / "o.csv", reg, min_confidence=2)


def test_export_fails_closed_on_empty_output(con, tmp_path, reg):
    src = tmp_path / "gnis.txt"
    src.write_text(
        GNIS_HEADER + "\n" + gnis_row("4", "Atlantic Ocean", "Sea", "35.2", "-75.5") + "\n"
    )
    with pytest.raises(ValueError, match="no rows"):
        gnis.convert(con, [("ME", src)], tmp_path / "out.csv", reg)


def test_oa_interp_writes_legacy_csv_and_skips_incomplete(con, tmp_path, reg):
    from pelias_prep import oa_interp

    src_dir = tmp_path / "openaddresses" / "us" / "me"
    src_dir.mkdir(parents=True)
    feats = [
        {"number": "2", "street": "King St", "coords": [-70.47, 44.13]},
        {"number": "", "street": "No Number Rd", "coords": [-70.47, 44.13]},
        {"number": "5", "street": "", "coords": [-70.47, 44.13]},
    ]
    lines = []
    for f in feats:
        props = {
            "hash": "h",
            "number": f["number"],
            "street": f["street"],
            "unit": "",
            "city": "Oxford",
            "district": "Oxford",
            "region": "ME",
            "postcode": "04270",
            "id": "",
            "accuracy": "",
        }
        geom = {"type": "Point", "coordinates": f["coords"]}
        lines.append(json.dumps({"type": "Feature", "properties": props, "geometry": geom}))
    (src_dir / "statewide.geojson").write_text("\n".join(lines) + "\n")

    results = oa_interp.convert_tree(con, tmp_path / "openaddresses", tmp_path / "out", reg)
    assert [r.rows for r in results] == [1]
    out = tmp_path / "out" / "us" / "me" / "statewide.csv"
    header = out.read_text().splitlines()[0]
    assert header == "LON,LAT,NUMBER,STREET,UNIT,CITY,DISTRICT,REGION,POSTCODE,ID,HASH"
    row = read_rows(out)[0]
    assert (row["NUMBER"], row["STREET"], row["LON"]) == ("2", "King St", "-70.47")
