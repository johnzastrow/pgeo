"""pgeo.feature_ac is a faithful copy of the non-address features, in the same physical order.

Autocomplete reads this narrow table instead of pgeo.feature when a request has no filters
(pgeo/sql/030_enrich_index.sql). If it drifts from its source, autocomplete quietly answers from
stale or partial data; if its row order drifts, ties between equal candidates fall differently
and results change with no logic having changed at all. Both happened during development - the
second because the table was first filled with INSERT, which backfills earlier pages, rather than
with CREATE TABLE AS - so both are pinned here, against whatever build is loaded.

Run against the local database; skipped when it is not reachable.
"""

from __future__ import annotations

import asyncio

import pytest

asyncpg = pytest.importorskip("asyncpg")

from pgeo.settings import Settings  # noqa: E402


def _val(sql: str):
    async def run():
        con = await asyncpg.connect(Settings.load().dsn, timeout=5)
        try:
            return await con.fetchval(sql)
        finally:
            await con.close()

    try:
        return asyncio.run(run())
    except (OSError, asyncpg.PostgresError, RuntimeError) as e:
        if isinstance(e, asyncpg.PostgresError) and not isinstance(e, asyncpg.InvalidPasswordError):
            raise
        pytest.skip(f"database not reachable: {e}")


def test_it_holds_every_non_address_feature_and_nothing_else():
    missing = _val("""SELECT count(*) FROM pgeo.feature f WHERE f.layer <> 'address'
                      AND NOT EXISTS (SELECT 1 FROM pgeo.feature_ac a WHERE a.id = f.id)""")
    extra = _val("""SELECT count(*) FROM pgeo.feature_ac a
                    WHERE NOT EXISTS (SELECT 1 FROM pgeo.feature f WHERE f.id = a.id AND f.layer <> 'address')""")
    assert (missing, extra) == (0, 0)


def test_it_holds_no_addresses():
    assert _val("SELECT count(*) FROM pgeo.feature_ac WHERE layer = 'address'") == 0


def test_every_copied_column_equals_its_source():
    """Ranking reads these five; a stale one would rank on old data."""
    differing = _val("""
        SELECT count(*) FROM pgeo.feature f JOIN pgeo.feature_ac a USING (id)
        WHERE a.importance IS DISTINCT FROM f.importance OR a.layer IS DISTINCT FROM f.layer
           OR a.label IS DISTINCT FROM f.label OR a.name_norm IS DISTINCT FROM f.name_norm
           OR a.tokens::text IS DISTINCT FROM f.tokens::text OR NOT ST_Equals(a.geom, f.geom)""")
    assert differing == 0


def test_its_rows_are_in_the_big_tables_physical_order():
    """Where equal candidates are cut, the order rows reach the sort decides which survive. The
    copy must present them in the order the original would. INSERT left 660 of 203,399 out of
    place and changed 12 of 4,018 golden results; CREATE TABLE AS ... ORDER BY ctid leaves none."""
    out_of_order = _val("""
        SELECT count(*) FROM (
          SELECT a.ctid > lag(a.ctid) OVER (ORDER BY f.ctid) AS ok
          FROM pgeo.feature f JOIN pgeo.feature_ac a USING (id) WHERE f.layer <> 'address') s
        WHERE ok IS FALSE""")
    assert out_of_order == 0


@pytest.mark.parametrize("index", ["feature_ac_tokens_idx", "feature_ac_name_trgm_idx", "feature_ac_pkey"])
def test_its_indexes_exist(index):
    assert _val(f"SELECT count(*) FROM pg_indexes WHERE schemaname = 'pgeo' AND indexname = '{index}'") == 1  # noqa: S608


def test_the_read_only_api_role_can_read_it():
    assert _val("SELECT has_table_privilege('pgeo_api', 'pgeo.feature_ac', 'SELECT')") is True


def test_it_is_a_quarter_the_width_of_its_source():
    """The point of the table. If someone adds the wide columns back, this says so."""
    narrow = _val("SELECT avg(pg_column_size(a.*)) FROM pgeo.feature_ac a")
    wide = _val("SELECT avg(pg_column_size(f.*)) FROM pgeo.feature f WHERE f.layer <> 'address'")
    assert narrow < wide / 3, f"feature_ac rows average {narrow:.0f} bytes against {wide:.0f}"
