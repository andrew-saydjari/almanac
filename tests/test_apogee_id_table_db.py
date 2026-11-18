"""
Live-database check of the maintainers' APOGEE ID -> sdss_id lookup table
(`catalogdb.mv_apogee_id_to_sdss_id`) against the live four-table join.

These tests talk to the SDSS database, so they only run when explicitly
requested (ALMANAC_DB_TESTS=1) and a connection is available; otherwise they
are skipped. They are read-only and query a few dozen identifiers.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALMANAC_DB_TESTS") != "1",
    reason="live database tests run only with ALMANAC_DB_TESTS=1",
)

TABLE = "catalogdb.mv_apogee_id_to_sdss_id"

# Plate-era 2MASS-style identifiers (prefix already stripped, as produced by
# `parse_target_identifier`), taken from an almanac run over MJD 57618-61230.
APO = [
    "08381883+6849289", "01480412+1247028", "03002278+0028365", "22441193-0057124",
    "16021298-2520379", "03562000+1737276", "09511766+0233127", "16493809-1916446",
    "13105218+3306541", "15031997+3201520",
]
LCO = [
    "07201390-6342433", "12294594-6617036", "18013868-2649154", "18101143-1440451",
    "16434521-4534572", "04244905-7054502", "17513627-1804554", "01323349-7041453",
    "18240658-3930254", "01452112-7108553",
]
# allStar apogee_id "AP00451807+4047096" has two rank-1 sdss_id (129046733,
# 129048422): two allStar rows (fields ANDR3, ANDR3shift) whose catalogids got
# separate sdss_ids at the same position. Which one a fiber gets depends on
# row order in both paths, so only the row sets are compared for it.
AMBIGUOUS = ["00451807+4047096"]
# Matched by the 2MASS fallback in almanac, not by allStar: in neither path.
NOT_IN_ALLSTAR = ["02164476+5246504"]
NO_MATCH = ["00000000+0000000"]


@pytest.fixture(scope="module")
def apogee():
    try:
        from almanac.database import is_database_available
    except Exception as e:  # pragma: no cover - depends on environment
        pytest.skip(f"database layer unavailable: {e}")
    if not is_database_available:
        pytest.skip("SDSS database not reachable")
    import almanac.apogee as apogee
    from almanac.database import database

    database.execute_sql("SET statement_timeout = '120s'")
    info = apogee._probe_view(TABLE)
    if not (info["exists"] and info["readable"]):
        pytest.skip(f"{TABLE} absent or unreadable")
    return apogee


def _candidates(apogee, identifiers):
    return sorted({c for i in identifiers for c in apogee.apogee_id_candidates(i)})


def _row_sets(rows):
    out = {}
    for sdss_id, apogee_id in rows:
        out.setdefault(apogee_id, set()).add(sdss_id)
    return out


def test_table_is_nonempty_and_indexed(apogee):
    info = apogee._probe_view(TABLE)
    assert info["nonempty"]
    assert info["kind"] in ("table", "materialized view")


def test_same_rows_as_live_join(apogee):
    candidates = _candidates(apogee, APO + LCO + AMBIGUOUS + NO_MATCH + NOT_IN_ALLSTAR)
    table = _row_sets(apogee._query_apogee_id_rows_from_view(TABLE, candidates))
    join = _row_sets(apogee._query_apogee_id_rows(candidates))
    assert table == join
    assert len(table["AP00451807+4047096"]) == 2


def test_same_sdss_id_as_live_join(apogee):
    identifiers = APO + LCO + NO_MATCH + NOT_IN_ALLSTAR
    candidates = _candidates(apogee, identifiers)
    table = apogee._add_apogee_id_rows({}, apogee._query_apogee_id_rows_from_view(TABLE, candidates))
    join = apogee._add_apogee_id_rows({}, apogee._query_apogee_id_rows(candidates))
    for identifier in APO + LCO:
        assert identifier in table, identifier
        assert table[identifier] == join[identifier] > 0, identifier
    for identifier in NO_MATCH + NOT_IN_ALLSTAR:
        assert identifier not in table and identifier not in join
