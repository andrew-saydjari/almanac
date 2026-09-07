"""Tests for CLI resume (--skip-existing) and run-level reporting wiring."""

import h5py as h5
import numpy as np
import pytest
from click.testing import CliRunner

import almanac.apogee as apogee
from almanac.cli import main

# MJD far before sdssdb coverage: no database connection is attempted and no
# raw data directory exists, so the run is fast and deterministic.
MJD = 51000


def run_cli(args):
    runner = CliRunner()
    return runner.invoke(main, args, catch_exceptions=False)


class TestSkipExisting:
    def test_no_data_run_writes_empty_missing_table(self, tmp_path):
        output = str(tmp_path / "out.h5")
        result = run_cli(["--mjd", str(MJD), "--apo", "--output", output])
        assert result.exit_code == 0
        with h5.File(output, "r") as fp:
            assert "missing_exposures" in fp
            assert len(fp["missing_exposures/mjd"]) == 0

    def test_skip_existing_skips_present_groups(self, tmp_path, monkeypatch):
        output = str(tmp_path / "out.h5")

        # Pre-populate the group for (apo, MJD) as if a previous run made it.
        with h5.File(output, "a") as fp:
            fp.create_group(f"raw/apo/{MJD}/exposures")

        calls = []
        original = apogee.get_almanac_data

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(apogee, "get_almanac_data", spy)

        result = run_cli(
            ["--mjd", str(MJD), "--apo", "--output", output, "--skip-existing"]
        )
        assert result.exit_code == 0
        assert calls == []  # nothing was reprocessed

    def test_without_skip_existing_reprocesses(self, tmp_path, monkeypatch):
        output = str(tmp_path / "out.h5")
        with h5.File(output, "a") as fp:
            fp.create_group(f"raw/apo/{MJD}/exposures")

        calls = []
        original = apogee.get_almanac_data

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(apogee, "get_almanac_data", spy)

        result = run_cli(["--mjd", str(MJD), "--apo", "--output", output])
        assert result.exit_code == 0
        assert len(calls) == 1

    def test_skip_existing_preserves_missing_table(self, tmp_path):
        """Skipped (obs, mjd) pairs keep their missing-table entries."""
        from almanac.io import read_missing_exposures, write_missing_exposures

        output = str(tmp_path / "out.h5")
        stale_row = dict(
            observatory="apo",
            mjd=MJD,
            exposure=3,
            expected_max_db=5,
            reason="db_no_file",
        )
        with h5.File(output, "a") as fp:
            fp.create_group(f"raw/apo/{MJD}/exposures")
            write_missing_exposures(fp, [stale_row])

        result = run_cli(
            ["--mjd", str(MJD), "--apo", "--output", output, "--skip-existing"]
        )
        assert result.exit_code == 0
        with h5.File(output, "r") as fp:
            assert read_missing_exposures(fp) == [stale_row]


class TestFaultIsolation:
    def test_failed_mjd_does_not_kill_the_run(self, tmp_path, monkeypatch):
        output = str(tmp_path / "out.h5")

        def boom(observatory, mjd, *args, **kwargs):
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(apogee, "get_almanac_data", boom)

        result = run_cli(["--mjd", str(MJD), "--apo", "--output", output])
        # The run completes (exit 0), records the failure, and still writes
        # the missing-exposures table.
        assert result.exit_code == 0
        with h5.File(output, "r") as fp:
            assert "missing_exposures" in fp
