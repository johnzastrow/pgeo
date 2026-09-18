"""Converter tests on tiny synthetic inputs (no network, no real data)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pelias_prep import gnis, overture, zcta
from pelias_prep.common import connect, load_polygon_wkt

GNIS_HEADER = (
    "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|county_numeric|"
    "map_name|date_created|date_edited|bgn_type|bgn_authority|bgn_date|prim_lat_dms|"
    "prim_long_dms|prim_lat_dec|prim_long_dec|source_lat_dms|source_long_dms|source_lat_dec|"
    "source_long_dec"
)


def gnis_row(fid: str, name: str, cls: str, lat: str, lon: str) -> str:
    # 21 fields: 9 leading, 6 empty (date_edited .. prim_long_dms), lat, lon, 4 empty.
    empty6, empty4 = "|" * 6, "|" * 4
    return f"{fid}|{name}|{cls}|Maine|23|Piscataquis|021|Map|01/01/1980{empty6}|{lat}|{lon}{empty4}"


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def con():
    c = connect()
    # A box roughly covering Maine stands in for the Census polygon.
    load_polygon_wkt(c, "POLYGON((-71.1 43.0, -66.9 43.0, -66.9 47.4, -71.1 47.4, -71.1 43.0))")
    return c


def test_gnis_maps_fields_and_drops_out_of_area_and_historical(con, tmp_path):
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
    res = gnis.convert(con, src, tmp_path / "out.csv")
    rows = read_rows(res.path)
    assert res.rows == 2
    assert [r["name"] for r in rows] == ["Moosehead Lake", "Katahdin"]
    assert rows[0]["source"] == "gnis"
    assert rows[0]["layer"] == "venue"
    assert rows[0]["category"] == "lake"
    assert json.loads(rows[0]["addendum_json_gnis"])["county"] == "Piscataquis"


def test_gnis_can_keep_historical(con, tmp_path):
    src = tmp_path / "gnis.txt"
    src.write_text(
        GNIS_HEADER
        + "\n"
        + gnis_row("3", "Old Mill (historical)", "Locale", "45.0", "-69.0")
        + "\n"
    )
    res = gnis.convert(con, src, tmp_path / "out.csv", include_historical=True)
    assert res.rows == 1


def test_zcta_keeps_only_maine_prefixes(con, tmp_path):
    src = tmp_path / "zcta.txt"
    src.write_text(
        "GEOID|GEOIDFQ|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG\n"
        "03801|x|1|1|1.0|0.1|43.07|-70.80\n"  # Portsmouth NH
        "03901|x|1|1|37.5|0.3|43.29|-70.84\n"  # Berwick ME
        "04101|x|1|1|2.0|0.5|43.66|-70.26\n"  # Portland ME
        "05001|x|1|1|1.0|0.1|43.66|-72.36\n"  # VT
    )
    res = zcta.convert(con, src, tmp_path / "out.csv")
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


def test_overture_filters_and_parses_address(con, tmp_path):
    src = tmp_path / "places.parquet"
    write_overture_parquet(con, src)
    res = overture.convert(con, src, tmp_path / "out.csv")
    rows = {r["id"]: r for r in read_rows(res.path)}
    # b: low confidence, c: closed, d: outside Maine -> dropped
    assert set(rows) == {"a", "e"}
    assert rows["a"]["housenumber"] == "123"
    assert rows["a"]["street"] == "Main St"
    assert rows["a"]["postcode"] == "04101"
    assert rows["a"]["category"] == "eat_and_drink"
    # No leading number -> no street/housenumber; non-Maine ZIP is dropped, not guessed.
    assert rows["e"]["housenumber"] == ""
    assert rows["e"]["street"] == ""
    assert rows["e"]["postcode"] == ""
    addendum = json.loads(rows["a"]["addendum_json_overture"])
    assert addendum["taxonomy"] == ["food_and_drink", "cafe"]


def test_overture_rejects_bad_parameters(con, tmp_path):
    with pytest.raises(ValueError):
        overture.convert(con, tmp_path / "x.parquet", tmp_path / "o.csv", min_confidence=2)


def test_export_fails_closed_on_empty_output(con, tmp_path):
    src = tmp_path / "gnis.txt"
    src.write_text(
        GNIS_HEADER + "\n" + gnis_row("4", "Atlantic Ocean", "Sea", "35.2", "-75.5") + "\n"
    )
    with pytest.raises(ValueError, match="no rows"):
        gnis.convert(con, src, tmp_path / "out.csv")


def test_oa_interp_writes_legacy_csv_and_skips_incomplete(con, tmp_path):
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

    results = oa_interp.convert_tree(con, tmp_path / "openaddresses", tmp_path / "out")
    assert [r.rows for r in results] == [1]
    out = tmp_path / "out" / "us" / "me" / "statewide.csv"
    header = out.read_text().splitlines()[0]
    assert header == "LON,LAT,NUMBER,STREET,UNIT,CITY,DISTRICT,REGION,POSTCODE,ID,HASH"
    row = read_rows(out)[0]
    assert (row["NUMBER"], row["STREET"], row["LON"]) == ("2", "King St", "-70.47")
