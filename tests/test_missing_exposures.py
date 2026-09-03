"""Tests for the missing-exposures product (workstream L3)."""

import numpy as np
import pytest

from almanac.apogee import (
    classify_missing_exposures,
    get_sequences,
    organize_exposures,
)
from almanac.data_models import Exposure


def make_exposures(observatory, mjd, exposure_numbers, image_type="object"):
    return [
        Exposure(
            observatory=observatory,
            mjd=mjd,
            exposure=n,
            image_type=image_type,
        )
        for n in exposure_numbers
    ]


def reasons_by_exposure(records):
    return {r["exposure"]: r["reason"] for r in records}


class TestOrganizeExposures:
    def test_dense_contract(self):
        """Rows must always be dense 1..N with holes filled as 'missing'."""
        exposures = make_exposures("apo", 61270, [1, 2, 5])
        organized = organize_exposures(exposures, n_expected=-1)
        assert [e.exposure for e in organized] == [1, 2, 3, 4, 5]
        assert [str(e.image_type) for e in organized] == [
            "object", "object", "missing", "missing", "object",
        ]

    def test_trailing_filled_from_expected(self):
        exposures = make_exposures("apo", 61270, [1, 2])
        organized = organize_exposures(exposures, n_expected=4)
        assert [e.exposure for e in organized] == [1, 2, 3, 4]
        assert str(organized[2].image_type) == "missing"
        assert str(organized[3].image_type) == "missing"

    def test_empty(self):
        assert organize_exposures([], n_expected=5) == []


class TestClassifyMissingExposures:
    def test_hole_in_db_is_db_no_file(self):
        """A known fixture hole: disk has [1, 2, 4], DB expects [1..5]."""
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2, 4]), n_expected=5
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, [1, 2, 3, 4, 5], "ok"
        )
        assert reasons_by_exposure(records) == {3: "db_no_file", 5: "db_no_file"}
        assert all(r["expected_max_db"] == 5 for r in records)
        assert all(r["observatory"] == "apo" for r in records)
        assert all(r["mjd"] == 61270 for r in records)

    def test_hole_not_in_db(self):
        """Numbering gap in both disk and DB: interior missing rows are holes."""
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2, 5]), n_expected=5
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, [1, 2, 5], "ok"
        )
        assert reasons_by_exposure(records) == {3: "hole", 4: "hole"}

    def test_trailing_and_db_no_file(self):
        """Disk has [1, 2]; DB expects [1, 2, 4]: 3 is trailing, 4 is db_no_file."""
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2]), n_expected=4
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, [1, 2, 4], "ok"
        )
        assert reasons_by_exposure(records) == {3: "trailing", 4: "db_no_file"}

    def test_file_no_db(self):
        """DB is missing a row for a file that exists on disk."""
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2, 3, 4]), n_expected=4
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, [1, 2, 4], "ok"
        )
        assert reasons_by_exposure(records) == {3: "file_no_db"}

    def test_db_unavailable_is_not_silent(self):
        """When the DB is down, holes are still reported and a sentinel row
        records that trailing exposures were undetectable."""
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2, 4]), n_expected=-1
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, None, "unavailable"
        )
        assert reasons_by_exposure(records) == {3: "hole", -1: "db_unavailable"}
        assert all(r["expected_max_db"] == -1 for r in records)

    def test_not_queried_pre_coverage(self):
        """MJDs before database coverage: holes only, no sentinel."""
        organized = organize_exposures(
            make_exposures("apo", 55000, [1, 3]), n_expected=-1
        )
        records = classify_missing_exposures(
            "apo", 55000, organized, None, "not_queried"
        )
        assert reasons_by_exposure(records) == {2: "hole"}

    def test_db_rows_but_no_files(self):
        """DB expects exposures but nothing on disk at all."""
        records = classify_missing_exposures("apo", 61270, [], [1, 2], "ok")
        assert reasons_by_exposure(records) == {1: "db_no_file", 2: "db_no_file"}

    def test_clean_night_is_empty(self):
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2, 3]), n_expected=3
        )
        records = classify_missing_exposures(
            "apo", 61270, organized, [1, 2, 3], "ok"
        )
        assert records == []


class TestGetExposuresAndMissing:
    """Wiring tests with the disk glob and database query monkeypatched."""

    def test_db_rows_without_files(self, monkeypatch):
        import almanac.apogee as apogee

        monkeypatch.setattr(apogee, "glob", lambda *_: [])
        monkeypatch.setattr(
            apogee,
            "get_expected_exposure_numbers",
            lambda obs, mjd: ([1, 2], "ok"),
        )
        exposures, missing = apogee.get_exposures_and_missing("apo", 61270)
        assert exposures == []
        assert reasons_by_exposure(missing) == {1: "db_no_file", 2: "db_no_file"}

    def test_db_unavailable_sentinel(self, monkeypatch):
        import almanac.apogee as apogee

        monkeypatch.setattr(apogee, "glob", lambda *_: [])
        monkeypatch.setattr(
            apogee,
            "get_expected_exposure_numbers",
            lambda obs, mjd: (None, "unavailable"),
        )
        exposures, missing = apogee.get_exposures_and_missing("apo", 61270)
        assert exposures == []
        # No disk data and no database: sentinel row only.
        assert reasons_by_exposure(missing) == {-1: "db_unavailable"}


class TestMissingSequences:
    def test_missing_sequence_ranges(self):
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 4, 7]), n_expected=8
        )
        missing = get_sequences(organized, "missing", ())
        assert missing == [(2, 3), (5, 6), (8, 8)]

    def test_no_missing(self):
        organized = organize_exposures(
            make_exposures("apo", 61270, [1, 2]), n_expected=-1
        )
        assert get_sequences(organized, "missing", ()) == []
