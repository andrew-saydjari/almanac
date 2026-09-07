"""Tests for the run-level /missing_exposures HDF5 table and sequence writing."""

import h5py as h5
import numpy as np
import pytest

from almanac.io import (
    read_missing_exposures,
    update,
    write_missing_exposures,
)
from almanac.data_models import Exposure


def row(observatory, mjd, exposure, expected_max_db, reason):
    return dict(
        observatory=observatory,
        mjd=mjd,
        exposure=exposure,
        expected_max_db=expected_max_db,
        reason=reason,
    )


class TestMissingExposuresTable:
    def test_write_and_read_roundtrip(self, tmp_path):
        rows = [
            row("apo", 61270, 3, 5, "db_no_file"),
            row("apo", 61270, 5, 5, "trailing"),
            row("lco", 61271, -1, -1, "db_unavailable"),
        ]
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_missing_exposures(fp, rows)
            group = fp["missing_exposures"]
            assert group["observatory"].dtype == np.dtype("S3")
            assert group["reason"].dtype == np.dtype("S16")
            assert group["mjd"].dtype == np.dtype(np.int64)
            assert group["exposure"].dtype == np.dtype(np.int64)
            assert group["expected_max_db"].dtype == np.dtype(np.int64)
            assert read_missing_exposures(fp) == sorted(
                rows, key=lambda r: (r["observatory"], r["mjd"], r["exposure"])
            )

    def test_empty_table_still_written(self, tmp_path):
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_missing_exposures(fp, [])
            assert "missing_exposures" in fp
            assert len(fp["missing_exposures/mjd"]) == 0
            assert read_missing_exposures(fp) == []

    def test_partial_rerun_merges(self, tmp_path):
        """Entries for (obs, mjd) pairs not processed in this run survive."""
        first = [
            row("apo", 61270, 3, 5, "db_no_file"),
            row("apo", 61271, 2, 2, "hole"),
        ]
        second = [row("apo", 61271, 4, 4, "trailing")]
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_missing_exposures(fp, first, replace_keys={("apo", 61270), ("apo", 61271)})
            # Second (partial) run only reprocessed 61271.
            write_missing_exposures(fp, second, replace_keys={("apo", 61271)})
            rows = read_missing_exposures(fp)
        assert rows == [
            row("apo", 61270, 3, 5, "db_no_file"),
            row("apo", 61271, 4, 4, "trailing"),
        ]

    def test_processed_clean_mjd_clears_stale_entries(self, tmp_path):
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_missing_exposures(fp, [row("apo", 61270, 3, 5, "db_no_file")])
            # Re-run processed 61270 and found nothing missing.
            write_missing_exposures(fp, [], replace_keys={("apo", 61270)})
            assert read_missing_exposures(fp) == []


class TestSequenceWriting:
    def test_missing_sequence_dataset(self, tmp_path):
        exposures = [
            Exposure(observatory="apo", mjd=61270, exposure=1, image_type="object"),
            Exposure(observatory="apo", mjd=61270, exposure=2, image_type="missing"),
            Exposure(observatory="apo", mjd=61270, exposure=3, image_type="object"),
        ]
        sequences = {"objects": [(1, 1), (3, 3)], "arclamps": [], "missing": [(2, 2)]}
        with h5.File(tmp_path / "test.h5", "a") as fp:
            update(fp, "apo", 61270, exposures, sequences)
            group = fp["raw/apo/61270/sequences"]
            assert list(group["missing"][:].ravel()) == [2, 2]
            # Empty sequences must still be integer-typed (start, end) pairs.
            assert group["arclamps"].shape == (0, 2)
            assert group["arclamps"].dtype == np.dtype(np.int64)
            assert group["missing"].dtype == np.dtype(np.int64)
