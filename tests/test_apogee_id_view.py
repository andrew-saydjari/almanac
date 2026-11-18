"""Tests for the optional materialized-view source of the APOGEE ID lookup."""

import logging
from datetime import date, datetime

import pytest

import almanac.apogee as apogee
from almanac.apogee import (
    SOURCE_LIVE_JOIN,
    SOURCE_VIEW,
    apogee_id_source_attrs,
    check_view_staleness,
    create_apogee_id_lookup,
    get_apogee_id_lookup,
    parse_view_refresh_date,
    resolve_apogee_id_source,
)

from tests.test_targeted_apogee_id_lookup import ALL_IDENTIFIERS, ROWS, FakeQueryLayer

VIEW = "catalogdb.mv_apogee_id_to_sdss_id"
COMMENT = (
    "apogee_id -> sdss_id (rank 1) for almanac. MUST be refreshed whenever any "
    "of those is updated. Last refreshed: 2026-12-01."
)
FRESH_STATS = [
    ("catalogdb.sdss_id_flat", datetime(2026, 11, 15, 16, 22), None, 0),
    ("catalogdb.sdss_id_to_catalog", datetime(2026, 11, 15, 15, 23), datetime(2026, 9, 22), 0),
    ("catalogdb.catalog_to_allstar_dr17_synspec_rev1", datetime(2026, 11, 2), datetime(2026, 8, 25), 0),
    ("catalogdb.allstar_dr17_synspec_rev1", datetime(2026, 11, 16), None, 0),
]


def _distinct(rows):
    """What the view holds: DISTINCT (apogee_id, sdss_id) pairs."""
    seen, out = set(), []
    for row in rows:
        if row not in seen:
            seen.add(row)
            out.append(row)
    return out


class FakeView:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, view, apogee_ids=None):
        assert view == VIEW
        self.calls.append(None if apogee_ids is None else list(apogee_ids))
        if apogee_ids is None:
            return list(self.rows)
        wanted = set(apogee_ids)
        return [row for row in self.rows if row[1] in wanted]


def _probe(exists=True, readable=True, nonempty=True, comment=COMMENT, stats=FRESH_STATS,
           kind="table"):
    def probe(view):
        assert view == VIEW
        return dict(
            exists=exists, readable=readable and exists, nonempty=nonempty and exists,
            kind=kind if exists else "",
            comment=comment if exists else None,
            refreshed=parse_view_refresh_date(comment) if exists else None,
            table_stats=stats if exists else [],
        )
    return probe


@pytest.fixture
def env(monkeypatch):
    """Fresh process state, fake join and fake view; returns a setup helper."""
    original = apogee.APOGEE_ID_LOOKUP
    apogee.APOGEE_ID_LOOKUP = None
    apogee.clear_targeted_apogee_id_lookup()
    apogee.force_live_apogee_id_join(False)
    monkeypatch.setattr(apogee.config, "apogee_id_lookup_view", VIEW, raising=False)
    join = FakeQueryLayer()
    monkeypatch.setattr(apogee, "_query_apogee_id_rows", join)

    def setup(view_rows=None, **probe_kwds):
        view = FakeView(_distinct(ROWS) if view_rows is None else view_rows)
        monkeypatch.setattr(apogee, "_query_apogee_id_rows_from_view", view)
        monkeypatch.setattr(apogee, "_probe_view", _probe(**probe_kwds))
        return join, view

    yield setup
    apogee.APOGEE_ID_LOOKUP = original
    apogee.clear_targeted_apogee_id_lookup()
    apogee.force_live_apogee_id_join(False)


def _restricted(full, identifiers):
    return {k: full[k] for k in identifiers if k in full}


class TestViewPresent:
    def test_reads_view_not_join(self, env):
        join, view = env()
        lookup = dict(get_apogee_id_lookup(ALL_IDENTIFIERS[:9]))
        assert view.calls and all(c is not None for c in view.calls)
        assert join.calls == []  # every identifier matched in the view
        assert lookup["00000001+0000001"] == 101
        source = resolve_apogee_id_source()
        assert source["source"] == SOURCE_VIEW
        assert source["view_last_refreshed"] == "2026-12-01"

    def test_resolved_once_per_process(self, env, monkeypatch):
        env()
        resolve_apogee_id_source()
        monkeypatch.setattr(apogee, "_probe_view", lambda view: pytest.fail("probed twice"))
        get_apogee_id_lookup(ALL_IDENTIFIERS)
        create_apogee_id_lookup()

    def test_full_build_reads_whole_view(self, env):
        join, view = env()
        full = create_apogee_id_lookup()
        assert view.calls == [None]
        assert join.calls == []
        assert full["00000003+0000003"] == 103

    def test_logs_comment(self, env, caplog):
        env()
        with caplog.at_level(logging.INFO):
            resolve_apogee_id_source()
        assert "Last refreshed: 2026-12-01" in caplog.text
        assert f"reading {VIEW} (table;" in caplog.text

    @pytest.mark.parametrize("kind", ["table", "materialized view"])
    def test_table_or_materialized_view_both_used(self, env, kind, caplog):
        # The maintainers delivered mv_apogee_id_to_sdss_id as an ordinary
        # table; a real materialized view must work the same way.
        join, view = env(kind=kind)
        with caplog.at_level(logging.INFO):
            source = resolve_apogee_id_source()
            lookup = get_apogee_id_lookup(ALL_IDENTIFIERS)
        assert source["source"] == SOURCE_VIEW
        assert source["view_kind"] == kind
        assert f"({kind};" in caplog.text
        assert view.calls
        assert lookup


class TestTieBreakSameOnBothPaths:
    def test_view_rows_reversed_equal_join(self, env, monkeypatch):
        # The table and the live join may return tied rows in different
        # orders; the larger sdss_id wins on both paths.
        join, view = env(view_rows=_distinct(ROWS)[::-1])
        from_view = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        apogee.clear_targeted_apogee_id_lookup()
        apogee.force_live_apogee_id_join(True)
        from_join = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert from_view == from_join
        assert from_view["00000005+0000005"] == 106


class TestViewAbsentFallback:
    def test_absent_view_uses_join(self, env, caplog):
        join, view = env(exists=False)
        with caplog.at_level(logging.INFO):
            lookup = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert view.calls == []
        assert join.calls
        assert lookup == _restricted(create_apogee_id_lookup(), ALL_IDENTIFIERS)
        source = resolve_apogee_id_source()
        assert source["source"] == SOURCE_LIVE_JOIN
        assert "does not exist" in source["reason"]
        assert "WARNING" not in [r.levelname for r in caplog.records]

    def test_probe_error_uses_join(self, env, monkeypatch, caplog):
        join, view = env()

        def boom(view):
            raise RuntimeError("permission denied for schema catalogdb")

        monkeypatch.setattr(apogee, "_probe_view", boom)
        with caplog.at_level(logging.WARNING):
            get_apogee_id_lookup(["00000001+0000001"])
        assert resolve_apogee_id_source()["source"] == SOURCE_LIVE_JOIN
        assert view.calls == [] and join.calls
        assert "permission denied" in caplog.text

    def test_unreadable_view_uses_join(self, env):
        join, view = env(readable=False)
        get_apogee_id_lookup(["00000001+0000001"])
        assert view.calls == [] and join.calls
        assert "not readable" in resolve_apogee_id_source()["reason"]

    def test_unconfigured_view_uses_join(self, env, monkeypatch):
        join, view = env()
        monkeypatch.setattr(apogee.config, "apogee_id_lookup_view", "", raising=False)
        get_apogee_id_lookup(["00000001+0000001"])
        assert view.calls == [] and join.calls

    def test_forced_live_join(self, env):
        join, view = env()
        source = resolve_apogee_id_source(force_live_join=True)
        assert source["source"] == SOURCE_LIVE_JOIN
        assert "forced" in source["reason"]
        get_apogee_id_lookup(["00000001+0000001"])
        create_apogee_id_lookup()
        assert view.calls == []


class TestViewEmptyOrIncomplete:
    def test_empty_view_uses_join(self, env, caplog):
        join, view = env(view_rows=[], nonempty=False)
        with caplog.at_level(logging.WARNING):
            lookup = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert resolve_apogee_id_source()["source"] == SOURCE_LIVE_JOIN
        assert "empty" in caplog.text
        assert view.calls == []
        assert lookup == _restricted(create_apogee_id_lookup(), ALL_IDENTIFIERS)

    def test_ids_missing_from_view_fall_back_to_join_with_warning(self, env, caplog):
        # A stale view lacking two apogee_ids that the live join has.
        stale = [r for r in _distinct(ROWS) if r[1] not in ("J00000002+0000002", "00000004+0000004")]
        join, view = env(view_rows=stale)
        with caplog.at_level(logging.WARNING):
            lookup = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert lookup["00000002+0000002"] == 102
        assert lookup["00000004+0000004"] == 104
        assert "stale" in caplog.text
        # The join was asked only about the identifiers the view missed.
        asked = set(join.calls[0])
        assert "J00000002+0000002" in asked and "2M00000001+0000001" not in asked

    def test_genuine_no_match_queries_join_once_without_warning(self, env, caplog):
        join, view = env()
        with caplog.at_level(logging.WARNING):
            get_apogee_id_lookup(["99999999+9999999"])
            get_apogee_id_lookup(["99999999+9999999"])
        assert len(join.calls) == 1  # memoised miss
        assert "stale" not in caplog.text


class TestIdenticalResultsBetweenSources:
    @pytest.mark.parametrize("batch_size", [1, 3, 1000])
    def test_targeted_view_equals_targeted_join(self, env, batch_size):
        env()
        via_view = dict(get_apogee_id_lookup(ALL_IDENTIFIERS, batch_size=batch_size))
        apogee.clear_targeted_apogee_id_lookup()
        apogee.force_live_apogee_id_join(True)
        via_join = dict(get_apogee_id_lookup(ALL_IDENTIFIERS, batch_size=batch_size))
        assert via_view == via_join

    def test_full_view_equals_full_join(self, env):
        env()
        via_view = create_apogee_id_lookup()
        apogee.force_live_apogee_id_join(True)
        via_join = create_apogee_id_lookup()
        assert via_view == via_join

    def test_bad_ap_entry_also_skipped_from_view(self, env):
        env()
        assert "" not in get_apogee_id_lookup([""])
        assert "" not in create_apogee_id_lookup()


class TestStaleness:
    def test_parse_refresh_date(self):
        assert parse_view_refresh_date(COMMENT) == date(2026, 12, 1)
        assert parse_view_refresh_date("last refreshed: 2026-01-02") == date(2026, 1, 2)
        assert parse_view_refresh_date("no date here") is None
        assert parse_view_refresh_date(None) is None
        assert parse_view_refresh_date("Last refreshed: 2026-13-40") is None

    def test_fresh(self):
        assert check_view_staleness(date(2026, 12, 1), FRESH_STATS) == []

    def test_analyzed_after_refresh(self):
        w = check_view_staleness(date(2026, 11, 10), FRESH_STATS)
        assert len(w) == 3  # three tables analyzed on/after 11-10
        assert any("sdss_id_flat" in m for m in w)

    def test_analyzed_on_refresh_day_is_silent(self):
        # Maintainers ANALYZE right after a rebuild: same day is not stale.
        assert check_view_staleness(date(2026, 11, 16), FRESH_STATS) == []

    def test_unanalyzed_modifications_are_silent(self):
        stats = list(FRESH_STATS)
        stats[0] = ("catalogdb.sdss_id_flat", datetime(2026, 11, 15), None, 7138)
        assert check_view_staleness(date(2026, 12, 1), stats) == []

    def test_no_refresh_date_is_silent(self):
        # AKS 2026-11-18: no warning for a missing "Last refreshed" comment
        # (the maintainers' table has none), even with reset statistics.
        stats = list(FRESH_STATS)
        stats[3] = ("catalogdb.allstar_dr17_synspec_rev1", None, None, 0)
        assert check_view_staleness(None, stats) == []
        assert check_view_staleness(None, FRESH_STATS) == []

    def test_missing_stats_row_is_silent(self):
        assert check_view_staleness(date(2026, 12, 1), FRESH_STATS[:3]) == []

    def test_no_comment_resolves_without_warning(self, env, caplog):
        env(comment=None)
        with caplog.at_level(logging.WARNING):
            source = resolve_apogee_id_source()
        assert source["source"] == SOURCE_VIEW
        assert source["staleness_warnings"] == []
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert apogee_id_source_attrs(source)["apogee_id_lookup_view_kind"] == "table"

    def test_stale_view_still_used_but_warned(self, env, caplog):
        env(comment="Last refreshed: 2026-10-01")
        with caplog.at_level(logging.WARNING):
            source = resolve_apogee_id_source()
        assert source["source"] == SOURCE_VIEW
        assert len(source["staleness_warnings"]) == 4
        assert "may be stale" in caplog.text
        assert [r.levelname for r in caplog.records if "stale" in r.message] == ["WARNING"] * 4


class TestOutputAttrs:
    def test_view_attrs(self, env):
        env()
        attrs = apogee_id_source_attrs(resolve_apogee_id_source())
        assert attrs["apogee_id_lookup_source"] == SOURCE_VIEW
        assert attrs["apogee_id_lookup_mode"] == "targeted"
        assert attrs["apogee_id_lookup_view"] == VIEW
        assert attrs["apogee_id_lookup_view_kind"] == "table"
        assert attrs["apogee_id_lookup_view_last_refreshed"] == "2026-12-01"
        assert attrs["apogee_id_lookup_staleness_warnings"] == ""
        assert all(isinstance(v, str) for v in attrs.values())

    def test_join_attrs(self, env):
        env(exists=False)
        attrs = apogee_id_source_attrs(resolve_apogee_id_source(), full=True)
        assert attrs["apogee_id_lookup_source"] == SOURCE_LIVE_JOIN
        assert attrs["apogee_id_lookup_mode"] == "full"
        assert attrs["apogee_id_lookup_view"] == ""
        assert attrs["apogee_id_lookup_view_kind"] == ""
        assert "does not exist" in attrs["apogee_id_lookup_note"]

    def test_none(self):
        assert apogee_id_source_attrs(None) == {}


class TestViewSQL:
    def test_quote_view_name(self):
        assert apogee._quote_view_name(VIEW) == '"catalogdb"."mv_apogee_id_to_sdss_id"'
        assert apogee._quote_view_name("mv_x") == '"mv_x"'
        for bad in ("a; DROP TABLE x", "a.b.c", 'a"b', "", "1abc"):
            with pytest.raises(ValueError):
                apogee._quote_view_name(bad)

    def test_view_queries(self, monkeypatch):
        calls = []
        monkeypatch.setattr(apogee, "_execute_sql", lambda sql, params=None: calls.append((sql, params)) or [])
        apogee._query_apogee_id_rows_from_view(VIEW, ["2M1", "J1"])
        apogee._query_apogee_id_rows_from_view(VIEW)
        assert calls[0] == (
            'SELECT sdss_id, apogee_id FROM "catalogdb"."mv_apogee_id_to_sdss_id" WHERE apogee_id = ANY(%s)',
            (["2M1", "J1"], ),
        )
        assert calls[1] == ('SELECT sdss_id, apogee_id FROM "catalogdb"."mv_apogee_id_to_sdss_id"', None)

    @pytest.mark.parametrize("relkind, kind", [("r", "table"), ("m", "materialized view"), ("v", "view"), ("x", "x"), (None, "")])
    def test_probe_kind(self, monkeypatch, relkind, kind):
        responses = iter([[(VIEW, )], [(True, None, relkind)], [(1, )], []])
        monkeypatch.setattr(apogee, "_execute_sql", lambda sql, params=None: next(responses))
        info = apogee._probe_view(VIEW)
        assert info["kind"] == kind
        assert info["comment"] is None and info["refreshed"] is None

    def test_probe_absent(self, monkeypatch):
        responses = iter([[(None, )]])
        monkeypatch.setattr(apogee, "_execute_sql", lambda sql, params=None: next(responses))
        info = apogee._probe_view(VIEW)
        assert info["exists"] is False and info["readable"] is False

    def test_probe_present(self, monkeypatch):
        responses = iter([
            [(VIEW, )],
            [(True, COMMENT, "r")],
            [(1, )],
            FRESH_STATS,
        ])
        sqls = []
        monkeypatch.setattr(apogee, "_execute_sql", lambda sql, params=None: sqls.append(sql) or next(responses))
        info = apogee._probe_view(VIEW)
        assert info["exists"] and info["readable"] and info["nonempty"]
        assert info["refreshed"] == date(2026, 12, 1)
        assert info["kind"] == "table"
        assert info["table_stats"] == FRESH_STATS
        assert "to_regclass" in sqls[0] and "pg_stat_all_tables" in sqls[3]
