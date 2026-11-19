"""Tests for the targeted (per-night) APOGEE ID -> SDSS ID lookup."""

import pytest

import almanac.apogee as apogee
from almanac.apogee import (
    apogee_id_candidates,
    create_apogee_id_lookup,
    get_apogee_id_lookup,
    parse_target_identifier,
    set_apogee_id_lookup,
)


# A fake allStar -> sdss_id table, as (sdss_id, apogee_id) rows in the order
# the database returns them. It exercises every prefix variant, the bad "AP"
# entry, duplicates and cross-prefix collisions.
ROWS = [
    (101, "2M00000001+0000001"),   # "2M" prefix
    (102, "J00000002+0000002"),    # "J" prefix
    (103, "AP00000003+0000003"),   # "AP" prefix
    (104, "00000004+0000004"),     # no prefix
    (57991461, "AP"),              # the bad entry (stripped key would be "")
    (57991462, "2M"),              # other too-short entries
    (57991463, "J "),
    (105, "2M00000005+0000005"),   # same apogee_id, two sdss_ids: smaller wins
    (106, "2M00000005+0000005"),
    (107, "2M00000006+0000006"),   # two apogee_ids giving the same key
    (108, "AP00000006+0000006"),   #   "00000006+0000006": smaller wins
    (109, "00000007+0000007"),     # bare key and a prefixed one: smaller wins
    (110, "J00000007+0000007"),
    (111, "2M00000008+0000008"),   # prefixed key and a bare one: smaller wins
    (112, "00000008+0000008"),
    (113, "2M00000009+0000009"),
    (113, "2M00000009+0000009"),   # exact duplicate row (join fan-out)
]

ALL_IDENTIFIERS = [
    "00000001+0000001",
    "00000002+0000002",
    "00000003+0000003",
    "00000004+0000004",
    "00000005+0000005",
    "00000006+0000006",
    "00000007+0000007",
    "00000008+0000008",
    "00000009+0000009",
    "99999999+9999999",   # no match
    "",                   # empty designation: must NOT hit the bad "AP" row
    "2M00000001+0000001", # unstripped form is also a key of the full lookup
]


class FakeQueryLayer:
    """Stands in for `_query_apogee_id_rows`: filters ROWS like the SQL would."""

    def __init__(self, rows=ROWS):
        self.rows = rows
        self.calls = []

    def __call__(self, apogee_ids=None):
        self.calls.append(None if apogee_ids is None else list(apogee_ids))
        if apogee_ids is None:
            return list(self.rows)
        wanted = set(apogee_ids)
        return [row for row in self.rows if row[1] in wanted]


@pytest.fixture
def fake(monkeypatch):
    original = apogee.APOGEE_ID_LOOKUP
    apogee.APOGEE_ID_LOOKUP = None
    apogee.clear_targeted_apogee_id_lookup()
    # These tests exercise the live-join path; the view source is covered in
    # test_apogee_id_view.py. Forcing it also keeps the tests off the database.
    apogee.force_live_apogee_id_join(True)
    layer = FakeQueryLayer()
    monkeypatch.setattr(apogee, "_query_apogee_id_rows", layer)
    yield layer
    apogee.APOGEE_ID_LOOKUP = original
    apogee.clear_targeted_apogee_id_lookup()
    apogee.force_live_apogee_id_join(False)


def _restricted(full, identifiers):
    return {k: full[k] for k in identifiers if k in full}


class TestCandidates:
    def test_candidates(self):
        assert apogee_id_candidates("123+45") == ("123+45", "2M123+45", "J123+45", "AP123+45")

    @pytest.mark.parametrize(
        "designation",
        ["2MASS-J00000001+0000001", "2MASS00000001+0000001", "2M00000001+0000001",
         "J00000001+0000001", "APG-J00000001+0000001", "AP00000001+0000001"],
    )
    def test_parsed_designations_resolve(self, fake, designation):
        class T:
            catalogid = 1
            twomass_designation = designation

        key, identifier = parse_target_identifier(T)
        assert key == "coordinate"
        assert identifier == "00000001+0000001"
        assert get_apogee_id_lookup([identifier])[identifier] == 101


class TestPrefixVariants:
    def test_each_prefix(self, fake):
        lookup = get_apogee_id_lookup(ALL_IDENTIFIERS[:4])
        assert lookup["00000001+0000001"] == 101  # from "2M..."
        assert lookup["00000002+0000002"] == 102  # from "J..."
        assert lookup["00000003+0000003"] == 103  # from "AP..."
        assert lookup["00000004+0000004"] == 104  # bare

    def test_unstripped_identifier(self, fake):
        assert get_apogee_id_lookup(["2M00000001+0000001"])["2M00000001+0000001"] == 101


class TestBadEntryGuard:
    def test_empty_identifier_does_not_match_bad_ap_row(self, fake):
        lookup = get_apogee_id_lookup(["", "00000001+0000001"])
        assert "" not in lookup
        assert lookup["00000001+0000001"] == 101

    def test_blank_identifier_is_not_queried(self, fake):
        # FPS nights often need only "" (an empty 2MASS designation): no query.
        assert get_apogee_id_lookup(["", "  "]) == {}
        assert fake.calls == []
        get_apogee_id_lookup(["", "00000001+0000001"])
        assert len(fake.calls) == 1 and "" not in fake.calls[0]

    def test_blank_skip_matches_query(self, fake):
        # Querying "" for real gives the same (empty) answer.
        rows = apogee._query_apogee_id_rows(apogee_id_candidates(""))
        assert {r[1] for r in rows} == {"AP", "2M"}
        assert "" not in apogee._add_apogee_id_rows({}, rows)

    def test_full_lookup_has_no_empty_key(self, fake):
        full = create_apogee_id_lookup()
        assert "" not in full
        assert "AP" not in full and "2M" not in full and "J " not in full
        assert 57991461 not in full.values()

    def test_short_identifier(self, fake):
        # Short but non-blank identifiers are queried; the bad short rows
        # ("AP", "2M", "J ") they could hit as candidates are still skipped.
        lookup = get_apogee_id_lookup(["AP", "2M", "J "])
        assert lookup == {}
        assert {"AP", "2M"} <= set(fake.calls[0])


class TestNoMatchAndDuplicates:
    def test_no_match(self, fake):
        lookup = get_apogee_id_lookup(["99999999+9999999"])
        assert "99999999+9999999" not in lookup
        assert lookup.get("99999999+9999999", -1) == -1

    def test_duplicates_smaller_sdss_id_wins(self, fake):
        lookup = get_apogee_id_lookup(
            ["00000005+0000005", "00000006+0000006", "00000007+0000007",
             "00000008+0000008", "00000009+0000009"]
        )
        assert lookup["00000005+0000005"] == 105
        assert lookup["00000006+0000006"] == 107
        assert lookup["00000007+0000007"] == 109
        assert lookup["00000008+0000008"] == 111
        assert lookup["00000009+0000009"] == 113


class TestEquivalenceWithFullLookup:
    def test_targeted_equals_full_restricted(self, fake):
        full = create_apogee_id_lookup()
        targeted = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert targeted == _restricted(full, ALL_IDENTIFIERS)

    @pytest.mark.parametrize("batch_size", [1, 2, 3, 5, 1000])
    def test_equal_for_any_batch_size(self, fake, batch_size):
        full = create_apogee_id_lookup()
        targeted = dict(get_apogee_id_lookup(ALL_IDENTIFIERS, batch_size=batch_size))
        assert targeted == _restricted(full, ALL_IDENTIFIERS)

    def test_equal_across_nights(self, fake):
        # Identifiers arriving over several nights, overlapping.
        full = create_apogee_id_lookup()
        get_apogee_id_lookup(ALL_IDENTIFIERS[:5])
        get_apogee_id_lookup(ALL_IDENTIFIERS[3:9])
        targeted = dict(get_apogee_id_lookup(ALL_IDENTIFIERS))
        assert targeted == _restricted(full, ALL_IDENTIFIERS)

    def test_randomised(self, monkeypatch):
        import random

        rng = random.Random(1234)
        stems = [f"{i:08d}+{i:07d}" for i in range(40)]
        rows = []
        for _ in range(300):
            stem = rng.choice(stems)
            prefix = rng.choice(["", "2M", "J", "AP", "2MASS-J"])
            rows.append((rng.randrange(1, 60), prefix + stem))
        rows += [(57991461, "AP"), (5, "J"), (6, "")]
        monkeypatch.setattr(apogee, "_query_apogee_id_rows", FakeQueryLayer(rows))
        original = apogee.APOGEE_ID_LOOKUP
        apogee.APOGEE_ID_LOOKUP = None
        apogee.force_live_apogee_id_join(True)
        try:
            full = create_apogee_id_lookup()
            for batch_size in (1, 7, 1000):
                apogee.clear_targeted_apogee_id_lookup()
                ids = rng.sample(stems, 25) + ["", "AP", "nope"] + ["2M" + stems[0]]
                targeted = dict(get_apogee_id_lookup(ids, batch_size=batch_size))
                assert targeted == _restricted(full, ids)
        finally:
            apogee.APOGEE_ID_LOOKUP = original
            apogee.clear_targeted_apogee_id_lookup()
            apogee.force_live_apogee_id_join(False)


class TestBatchingAndMemoisation:
    def test_batching(self, fake):
        ids = ALL_IDENTIFIERS[:9]
        get_apogee_id_lookup(ids, batch_size=4)
        assert len(fake.calls) == 3
        # Each query carries the candidates of at most 4 identifiers.
        assert all(len(c) <= 4 * 4 for c in fake.calls)
        assert all(c is not None for c in fake.calls)  # never the full query

    def test_memoised_hits_and_misses(self, fake):
        get_apogee_id_lookup(["00000001+0000001", "99999999+9999999"])
        assert len(fake.calls) == 1
        lookup = get_apogee_id_lookup(["00000001+0000001", "99999999+9999999"])
        assert len(fake.calls) == 1  # nothing re-queried, not even the miss
        assert lookup["00000001+0000001"] == 101
        get_apogee_id_lookup(["00000001+0000001", "00000002+0000002"])
        assert len(fake.calls) == 2
        assert fake.calls[1] == sorted(apogee_id_candidates("00000002+0000002"))

    def test_empty_request_does_not_query(self, fake):
        assert get_apogee_id_lookup([]) == {}
        assert fake.calls == []

    def test_only_requested_keys_are_stored(self, fake):
        lookup = get_apogee_id_lookup(["00000001+0000001"])
        # The full "2M..." key was produced by the fetched row but not requested.
        assert set(lookup) == {"00000001+0000001"}

    def test_clear(self, fake):
        get_apogee_id_lookup(["00000001+0000001"])
        apogee.clear_targeted_apogee_id_lookup()
        get_apogee_id_lookup(["00000001+0000001"])
        assert len(fake.calls) == 2


class TestFullLookupOption:
    def test_installed_full_lookup_is_used_without_queries(self, fake):
        full = create_apogee_id_lookup()
        fake.calls.clear()
        set_apogee_id_lookup(full)
        assert get_apogee_id_lookup(ALL_IDENTIFIERS) is full
        assert fake.calls == []

    def test_full_lookup_matches_old_construction(self, fake):
        # The pre-PR loop, verbatim, on the same rows sorted by descending
        # sdss_id (so "last row wins" there is "smaller sdss_id wins").
        old = {}
        for sdss_id, apogee_id in sorted(ROWS, reverse=True):
            if len(apogee_id.strip()) <= 2:
                continue
            old[apogee_id] = sdss_id
            for prefix in ("2M", "J", "AP"):
                if apogee_id.startswith(prefix):
                    old[apogee_id[len(prefix):]] = sdss_id
        assert create_apogee_id_lookup() == old
        assert fake.calls == [None]


class TestQuerySQL:
    """The real peewee queries, compiled (not executed)."""

    def _sql(self, monkeypatch, apogee_ids):
        pytest.importorskip("sdssdb")
        captured = {}
        monkeypatch.setattr(apogee, "_execute_query", lambda q: captured.setdefault("q", q) and [])
        try:
            apogee._query_apogee_id_rows(apogee_ids)
        except Exception as e:  # pragma: no cover - environment without catalogdb
            pytest.skip(f"catalogdb models unavailable: {e}")
        return captured["q"].sql()

    def test_targeted_sql(self, monkeypatch):
        sql, params = self._sql(monkeypatch, ["2M1", "J1"])
        assert 'FROM "catalogdb"."allstar_dr17_synspec_rev1"' in sql
        assert '"apogee_id" IN (%s, %s)' in sql
        assert '"rank" = %s' in sql
        assert params == ["2M1", "J1", 1]
        for table in ("catalog_to_allstar_dr17_synspec_rev1", "sdss_id_to_catalog", "sdss_id_flat"):
            assert table in sql

    def test_full_sql(self, monkeypatch):
        sql, params = self._sql(monkeypatch, None)
        assert 'FROM "catalogdb"."sdss_id_flat"' in sql
        assert " IN " not in sql
        assert params == [1]


class TestSmallerSdssIdTieBreak:
    """AKS 2026-11-18: among several equally (rank-1) ranked sdss_id for one
    identifier, take the smaller (AKS 2026-11-19: the convention of other
    SDSS pipelines). (ROWS carry no rank, i.e. all rank 1.)"""

    @pytest.mark.parametrize("order", ["ascending", "descending", "shuffled"])
    def test_independent_of_row_order(self, order):
        import random
        rows = list(ROWS)
        if order == "descending":
            rows = rows[::-1]
        elif order == "shuffled":
            random.Random(3).shuffle(rows)
        expected = apogee._add_apogee_id_rows({}, sorted(ROWS))
        assert apogee._add_apogee_id_rows({}, rows) == expected
        assert expected["00000005+0000005"] == 105
        assert expected["2M00000005+0000005"] == 105
        assert expected["00000006+0000006"] == 107
        assert expected["00000007+0000007"] == 109
        assert expected["00000008+0000008"] == 111

    def test_real_tie_example(self):
        # allStar AP00451807+4047096 (fields ANDR3, ANDR3shift) has two
        # rank-1 sdss_id; either row order gives the smaller one.
        rows = [(129048422, "AP00451807+4047096"), (129046733, "AP00451807+4047096")]
        for r in (rows, rows[::-1]):
            lookup = apogee._add_apogee_id_rows({}, r)
            assert lookup["00451807+4047096"] == 129046733
            assert lookup["AP00451807+4047096"] == 129046733

    def test_targeted_lookup_independent_of_row_order(self, monkeypatch):
        results = []
        for reverse in (False, True):
            fake = FakeQueryLayer(rows=ROWS[::-1] if reverse else ROWS)
            monkeypatch.setattr(apogee, "_query_apogee_id_rows", fake)
            apogee.APOGEE_ID_LOOKUP = None
            apogee.clear_targeted_apogee_id_lookup()
            apogee.force_live_apogee_id_join(True)
            results.append(dict(get_apogee_id_lookup(ALL_IDENTIFIERS)))
        apogee.clear_targeted_apogee_id_lookup()
        apogee.force_live_apogee_id_join(False)
        assert results[0] == results[1]

    def test_best_ranked_lookup_equal_rank(self):
        rows = [(5, 20, 1), (5, 10, 1), (6, 1, 1), (5, 30, 1), (6, 2, 1)]
        assert apogee.best_ranked_lookup(rows) == {5: 10, 6: 1}
        assert apogee.best_ranked_lookup(rows[::-1]) == {5: 10, 6: 1}
        assert apogee.best_ranked_lookup([]) == {}


class TestRankBeforeSdssId:
    """
    AKS 2026-11-18: "For the tie break, that should only be if there are
    multiple rank 1 entries." Best (lowest) rank wins; the smaller sdss_id
    only breaks a tie among rows of equal best rank (AKS 2026-11-19). An
    sdss_id with a worse rank must NOT win, whatever its size.
    """

    @pytest.mark.parametrize("reverse", [False, True])
    def test_sdss_id_with_worse_rank_loses(self, reverse):
        # Real shape: catalogid 63050395066243429 has sdss_id 70149305
        # (rank 1) and 70149306 (rank 2).
        rows = [(63050395066243429, 70149305, 1), (63050395066243429, 70149306, 2)]
        if reverse:
            rows = rows[::-1]
        assert apogee.best_ranked_lookup(rows) == {63050395066243429: 70149305}
        # Same with the sizes swapped: the rank-2 one is smaller and must
        # still lose (the smaller-sdss_id rule is a tie-break only).
        rows = [("g", 70149306, 1), ("g", 70149305, 2)]
        if reverse:
            rows = rows[::-1]
        assert apogee.best_ranked_lookup(rows) == {"g": 70149306}

    @pytest.mark.parametrize("reverse", [False, True])
    def test_tie_among_best_rank_takes_smaller(self, reverse):
        rows = [("g", 10, 1), ("g", 3, 2), ("g", 20, 1), ("g", 1, 3)]
        if reverse:
            rows = rows[::-1]
        # 3 and 1 are smaller but worse-ranked; among rank 1, 10 < 20.
        assert apogee.best_ranked_lookup(rows) == {"g": 10}

    def test_single_best_rank_row_wins_regardless_of_size(self):
        rows = [("g", 1000, 1)] + [("g", 1 + i, 2) for i in range(5)]
        assert apogee.best_ranked_lookup(rows) == {"g": 1000}

    def test_missing_rank_is_worst(self):
        assert apogee.best_ranked_lookup([("g", 5, None), ("g", 50, 2)]) == {"g": 50}

    @pytest.mark.parametrize("reverse", [False, True])
    def test_apogee_id_rows_rank_precedence(self, reverse):
        rows = [
            (50, "2M00000010+0000010", 2),    # smaller, worse rank
            (100, "2M00000010+0000010", 1),
            (30, "AP00000010+0000010", 3),    # other apogee_id, same stripped key
            (150, "2M00000011+0000011", 1),   # genuine rank-1 tie: smaller wins
            (140, "AP00000011+0000011", 1),
        ]
        if reverse:
            rows = rows[::-1]
        lookup = apogee._add_apogee_id_rows({}, rows)
        assert lookup["00000010+0000010"] == 100
        assert lookup["2M00000010+0000010"] == 100
        assert lookup["AP00000010+0000010"] == 30  # its only row
        assert lookup["00000011+0000011"] == 140
        assert lookup["AP00000011+0000011"] == 140

    def test_rows_without_rank_count_as_rank_1(self):
        # The lookup table has no rank column (rank-1 pairs only).
        rows = [(50, "2M00000010+0000010", 2), (100, "2M00000010+0000010")]
        assert apogee._add_apogee_id_rows({}, rows)["00000010+0000010"] == 100
        rows = [(200, "2M00000010+0000010"), (100, "2M00000010+0000010")]
        assert apogee._add_apogee_id_rows({}, rows)["00000010+0000010"] == 100

    def test_targeted_lookup_rank_precedence(self, monkeypatch):
        rows = [
            (50, "2M00000010+0000010", 2),
            (100, "2M00000010+0000010", 1),
            (150, "2M00000011+0000011", 1),
            (140, "AP00000011+0000011", 1),
        ]
        results = []
        for r in (rows, rows[::-1]):
            monkeypatch.setattr(apogee, "_query_apogee_id_rows", FakeQueryLayer(rows=r))
            apogee.APOGEE_ID_LOOKUP = None
            apogee.clear_targeted_apogee_id_lookup()
            apogee.force_live_apogee_id_join(True)
            results.append(dict(get_apogee_id_lookup(["00000010+0000010", "00000011+0000011"])))
        apogee.clear_targeted_apogee_id_lookup()
        apogee.force_live_apogee_id_join(False)
        assert results[0] == results[1] == {"00000010+0000010": 100, "00000011+0000011": 140}

    @pytest.mark.parametrize(
        "helper", ["catalogid_sdss_id_lookup", "gaia_dr2_sdss_id_lookup", "twomass_sdss_id_lookup"]
    )
    def test_path_helpers_apply_rank_rule(self, helper, monkeypatch):
        # The query result is replaced; checks the fold each path uses.
        pytest.importorskip("peewee")
        # "k": the worse-ranked sdss_id is the smaller one and must lose.
        rows = [("k", 70149304, 2), ("k", 70149305, 1), ("t", 5, 1), ("t", 7, 1)]
        monkeypatch.setattr(apogee, "_execute_query", lambda q: rows)
        try:
            out = getattr(apogee, helper)(["k", "t"])
        except Exception as e:  # pragma: no cover - needs sdssdb models
            pytest.skip(f"catalogdb models unavailable: {e}")
        assert out == {"k": 70149305, "t": 5}
