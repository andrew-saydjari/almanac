"""`almanac add fibers`: the same night pipeline as `almanac --fibers`, from a stored file.

Adapted from upstream sdss/almanac #66; rewired so that both commands run
`apogee.process_night` and the same writers. No database or raw data needed.
"""

import os

import h5py as h5
import numpy as np
import pytest

from almanac import apogee, io
from almanac.overrides import rewind_exposure_overrides
from tests.helpers import MJD, SEQUENCES, fake_targets, make_exposures, run_cli, write_night


def _fake_process_night(observatory, mjd, exposures, missing=(), fibers=False, meta=False,
                        only_assign_sdss_id_to_first_exposure_per_configuration=True,
                        overrides=True, mutate=None):
    """Stand-in for the real pipeline: give object exposures targets (no files, no DB)."""
    for e in exposures:
        e._targets = fake_targets(e) if e.image_type == "object" else tuple()
    if mutate:
        mutate(exposures)
    sequences = {
        "objects": apogee.get_science_sequences(exposures),
        "arclamps": apogee.get_arclamp_sequences(exposures),
        "missing": apogee.get_sequences(exposures, "missing", ()),
    }
    return observatory, mjd, exposures, sequences, list(missing)


class TestSafety:
    def test_needs_output_or_in_place(self, tmp_path):
        path = str(tmp_path / "night.h5")
        write_night(path)
        result = run_cli(["add", "fibers", path])
        assert result.exit_code != 0
        assert "--output" in result.output and "--in-place" in result.output

    def test_not_both(self, tmp_path):
        path = str(tmp_path / "night.h5")
        write_night(path)
        result = run_cli(["add", "fibers", path, "--in-place", "--output", str(tmp_path / "o.h5")])
        assert result.exit_code != 0

    def test_output_must_be_new(self, tmp_path):
        path = str(tmp_path / "night.h5")
        out = tmp_path / "exists.h5"
        out.write_bytes(b"")
        write_night(path)
        result = run_cli(["add", "fibers", path, "--output", str(out)])
        assert result.exit_code != 0
        assert "exists" in result.output


class TestEndToEnd:
    def test_output_copy_gets_fibers_and_input_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(apogee, "process_night", _fake_process_night)
        path = str(tmp_path / "night.h5")
        out = str(tmp_path / "with_fibers.h5")
        write_night(path)
        before = open(path, "rb").read()
        result = run_cli(["add", "fibers", path, "--output", out, "--no-x-match", "-p", "1"])
        assert result.exit_code == 0, result.output
        assert open(path, "rb").read() == before
        with h5.File(out, "r") as fp:
            assert list(fp[f"raw/apo/{MJD}/fibers"]) == ["123"]
            assert fp[f"raw/apo/{MJD}/fibers/123/fiber_id"][:].tolist() == [1, 2]
            assert fp[f"raw/apo/{MJD}"].attrs["exposure_overrides"] == "enabled"
            assert not any(a.startswith("apogee_id_lookup_") for a in fp[f"raw/apo/{MJD}"].attrs)

    def test_matches_a_direct_fibers_write(self, tmp_path, monkeypatch):
        """Same writers: the add-fibers file equals io.update(fibers=True) on the same night."""
        monkeypatch.setattr(apogee, "process_night", _fake_process_night)
        path = str(tmp_path / "night.h5")
        write_night(path)
        run_cli(["add", "fibers", path, "--in-place", "--no-x-match", "-p", "1"])

        direct = str(tmp_path / "direct.h5")
        exposures = make_exposures()
        _fake_process_night("apo", MJD, exposures)
        with h5.File(direct, "a") as fp:
            io.update(fp, "apo", MJD, exposures, SEQUENCES, fibers=True)
        with h5.File(path, "r") as a, h5.File(direct, "r") as b:
            ga, gb = a[f"raw/apo/{MJD}/fibers/123"], b[f"raw/apo/{MJD}/fibers/123"]
            assert set(ga) == set(gb)
            for k in ga:
                va, vb = ga[k][()], gb[k][()]
                if va.dtype.kind == "f":
                    assert np.array_equal(va, vb, equal_nan=True), k
                else:
                    assert np.array_equal(va, vb), k

    def test_night_whose_exposures_would_change_is_refused(self, tmp_path, monkeypatch):
        def changes_a_column(*args, **kwargs):
            return _fake_process_night(
                *args, **kwargs,
                mutate=lambda es: setattr(es[0], "override_ids", "some-new-override"),
            )

        monkeypatch.setattr(apogee, "process_night", changes_a_column)
        path = str(tmp_path / "night.h5")
        write_night(path)
        result = run_cli(["add", "fibers", path, "--in-place", "--no-x-match", "-p", "1"])
        assert result.exit_code == 5  # ExitStatus.NIGHT_REFUSED
        with h5.File(path, "r") as fp:
            assert f"raw/apo/{MJD}/fibers" not in fp

    def test_replaces_every_fiber_table_and_stale_lookup_attrs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(apogee, "process_night", _fake_process_night)
        path = str(tmp_path / "night.h5")
        write_night(path)
        with h5.File(path, "a") as fp:
            fp.create_group(f"raw/apo/{MJD}/fibers/999").create_dataset("sdss_id", data=[1])
            fp[f"raw/apo/{MJD}"].attrs["apogee_id_lookup_source"] = "stale"
        assert run_cli(["add", "fibers", path, "--in-place", "--no-x-match", "-p", "1"]).exit_code == 0
        with h5.File(path, "r") as fp:
            assert list(fp[f"raw/apo/{MJD}/fibers"]) == ["123"]
            assert "apogee_id_lookup_source" not in fp[f"raw/apo/{MJD}"].attrs


class TestSharedPath:
    def test_get_almanac_data_runs_process_night(self, monkeypatch):
        calls = []
        monkeypatch.setattr(apogee, "get_exposures_and_missing", lambda o, m: (["E"], ["M"]))
        monkeypatch.setattr(apogee, "process_night", lambda *a, **k: calls.append((a, k)) or "result")
        assert apogee.get_almanac_data("apo", MJD, True, True, overrides=False) == "result"
        (args, kwargs), = calls
        assert args == ("apo", MJD, ["E"], ["M"])
        assert kwargs["fibers"] and kwargs["meta"] and not kwargs["overrides"]

    def test_worker_rewinds_then_runs_process_night(self, monkeypatch):
        from almanac import cli

        seen = {}

        def spy(observatory, mjd, exposures, missing=(), **kwargs):
            seen["plate_ids"] = [e.plate_id for e in exposures]
            seen["kwargs"] = kwargs
            return observatory, mjd, exposures, {}, []

        monkeypatch.setattr(apogee, "process_night", spy)
        exposures = make_exposures()
        exposures[0].config_id = 1_000_000_123
        exposures[0]._original_config_id = 123
        cli._add_fibers_task("apo", MJD, exposures, True, True)
        assert exposures[0].config_id == 123  # rewound before the pipeline ran
        assert seen["kwargs"] == dict(fibers=True, meta=True, overrides=True)


class TestReadBackAndRewind:
    def test_read_exposures_round_trip_with_synthetic_id(self, tmp_path):
        path = str(tmp_path / "night.h5")
        exposures = make_exposures()
        exposures[1]._original_config_id = 123
        exposures[1].config_id = 1_000_000_124
        exposures[1].override_ids = "x"
        exposures[1].override_actions = "retarget_fibers(1=Vesta;others=keep)"
        with h5.File(path, "a") as fp:
            io.update(fp, "apo", MJD, exposures, SEQUENCES, fibers=False)
        with h5.File(path, "r") as fp:
            read = io.read_exposures(fp, "apo", MJD)
            assert io.compare_exposures(fp[f"raw/apo/{MJD}/exposures"], read) == []
            assert io.compare_sequences(fp, "apo", MJD, SEQUENCES) == []
            assert io.compare_sequences(fp, "apo", MJD, {**SEQUENCES, "objects": [(1, 1)]}) == ["sequences/objects differ"]
        assert read[1].config_id == 1_000_000_124
        assert read[1].original_config_id == 123
        assert read[0].original_config_id == read[0].config_id == 123
        assert read[0].prefix == "apR"

        rewind_exposure_overrides(read)
        assert read[1].config_id == 123
        assert read[1].original_config_id == 123
        assert read[1].override_ids == "" and read[1].override_actions == ""

    def test_read_exposures_missing_group(self, tmp_path):
        path = str(tmp_path / "empty.h5")
        with h5.File(path, "a") as fp:
            assert io.read_exposures(fp, "apo", MJD) == []

    def test_write_night_attrs_keeps_lookup_attrs_unless_asked(self, tmp_path):
        path = str(tmp_path / "night.h5")
        write_night(path)
        with h5.File(path, "a") as fp:
            fp[f"raw/apo/{MJD}"].attrs["apogee_id_lookup_mode"] = "targeted"
            io.write_night_attrs(fp, "apo", MJD, {"exposure_overrides": "disabled"}, {})
            assert "apogee_id_lookup_mode" in fp[f"raw/apo/{MJD}"].attrs
            io.write_night_attrs(fp, "apo", MJD, {}, {}, clear_stale_lookup_attrs=True)
            assert "apogee_id_lookup_mode" not in fp[f"raw/apo/{MJD}"].attrs


def test_failed_night_exit_status(tmp_path, monkeypatch):
    """A night whose processing raises: ExitStatus.NIGHT_EXCEPTION (6)."""
    def boom(*args, **kwargs):
        raise ValueError("No table named PLUGMAPOBJ")

    monkeypatch.setattr(apogee, "process_night", boom)
    path = str(tmp_path / "night.h5")
    write_night(path)
    result = run_cli(["add", "fibers", path, "--in-place", "--no-x-match", "-p", "1"])
    assert result.exit_code == 6
