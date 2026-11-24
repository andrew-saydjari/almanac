"""End-of-run summary and exit status of the main command (bug A)."""

import h5py as h5
import pytest
from click.testing import CliRunner
from peewee import OperationalError

import almanac.apogee as apogee
from almanac import io
from almanac.cli import _failure_category, _run_exit_status, main
from almanac.exit_codes import CATEGORY_EXIT_STATUS, ExitStatus, exit_status_for
from tests.helpers import SEQUENCES, fake_targets, make_exposures

MJD = 60000


def run_cli(args):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def db_unavailable_row(observatory, mjd):
    return dict(observatory=observatory, mjd=mjd, exposure=-1, expected_max_db=-1,
                reason="db_unavailable")


@pytest.fixture
def fake_nights(monkeypatch):
    """
    Replace the per-night pipeline: `behaviour[(obs, mjd)]` is one of
    "data", "no_data", "db_unavailable" (data, but opsdb not queried),
    "plugmap" (exception), "database" (database error).
    """
    behaviour = {}

    def fake(observatory, mjd, fibers=False, meta=False, **kwargs):
        kind = behaviour.get((observatory, mjd), "no_data")
        if kind == "plugmap":
            raise ValueError("No table named PLUGMAPOBJ in table file")
        if kind == "database":
            raise OperationalError("server closed the connection unexpectedly")
        if kind == "no_data":
            return observatory, mjd, [], {}, []
        missing = [db_unavailable_row(observatory, mjd)] if kind == "db_unavailable" else []
        exposures = make_exposures(observatory, mjd)
        if fibers:
            # The real night path loads (and cross-matches) the targets in the
            # worker (`apogee.process_night`); do the same here, so the writer
            # never reads confSummary files (no sdsscore checkout in CI).
            for e in exposures:
                e._targets = fake_targets(e) if e.image_type == "object" else ()
        return observatory, mjd, exposures, dict(SEQUENCES), missing

    monkeypatch.setattr(apogee, "get_almanac_data", fake)
    # No database in the tests: the parent-side cross-match setup is a no-op.
    monkeypatch.setattr(apogee, "prepare_cross_match", lambda *a, **k: (None, {}, None))
    return behaviour


def test_failure_category():
    assert _failure_category(ValueError("No table named PLUGMAPOBJ")) == "exception"
    assert _failure_category(OperationalError("gone")) == "database_error"
    assert _failure_category(apogee.DatabaseUnavailableError("x")) == "database_error"


def test_exit_status_rules():
    failed = [("apo", 1, "exception", "boom")]
    assert _run_exit_status([], set(), True) == 0
    assert _run_exit_status(failed, set(), False) == ExitStatus.NIGHT_EXCEPTION
    assert _run_exit_status(failed, {("apo", 2)}, True) == ExitStatus.NIGHT_EXCEPTION
    assert _run_exit_status([], {("apo", 2)}, True) == ExitStatus.DB_UNAVAILABLE_CROSS_MATCH
    assert _run_exit_status([], {("apo", 2)}, False) == ExitStatus.DB_UNAVAILABLE
    mixed = failed + [("apo", 3, "pool_broken", "x"), ("apo", 4, "write_failed", "y")]
    assert _run_exit_status(mixed, {("apo", 2)}, True) == ExitStatus.POOL_BROKEN


def test_exit_codes_one_per_category_increasing():
    codes = [int(c) for c in ExitStatus if c]
    assert codes == sorted(codes) and len(set(codes)) == len(codes)
    assert min(codes) == 3  # 1 and 2 belong to click
    assert all(c < 128 for c in codes)
    assert set(CATEGORY_EXIT_STATUS.values()) == {c for c in ExitStatus if c}
    for category, status in CATEGORY_EXIT_STATUS.items():
        assert exit_status_for({category}) == status  # alone
    assert exit_status_for(set(CATEGORY_EXIT_STATUS)) == max(ExitStatus)  # all: highest
    assert exit_status_for(set()) == ExitStatus.OK


def test_all_good_and_no_data_exit_0(tmp_path, fake_nights):
    out = str(tmp_path / "a.h5")
    fake_nights[("apo", MJD)] = "data"
    result = run_cli(["--mjds", f"{MJD},{MJD + 1}", "--apo", "-O", out, "-p", "1"])
    assert result.exit_code == 0
    with h5.File(out, "r") as fp:
        attrs = dict(fp["run_summary"].attrs)
        assert io.read_run_summary(fp) == []
    assert attrs["last_run_n_requested"] == 2
    assert attrs["last_run_n_with_data"] == 1
    assert attrs["last_run_n_no_data"] == 1
    assert attrs["last_run_n_failed"] == 0
    assert attrs["last_run_exit_status"] == 0 and attrs["last_run_exit_status_name"] == "OK"


def test_failed_nights_exit_3_and_are_recorded(tmp_path, fake_nights):
    out = str(tmp_path / "a.h5")
    fake_nights[("apo", MJD)] = "data"
    fake_nights[("apo", MJD + 1)] = "plugmap"
    fake_nights[("apo", MJD + 2)] = "database"
    result = run_cli(["--mjd-start", str(MJD), "--mjd-end", str(MJD + 2), "--apo", "-O", out, "-p", "1"])
    # exception (6) and database_error (7) in one run: the highest.
    assert result.exit_code == ExitStatus.DATABASE_ERROR
    with h5.File(out, "r") as fp:
        rows = io.read_run_summary(fp)
        attrs = dict(fp["run_summary"].attrs)
        assert f"raw/apo/{MJD}/exposures" in fp
    assert [(r["mjd"], r["category"]) for r in rows] == [
        (MJD + 1, "exception"), (MJD + 2, "database_error")
    ]
    assert "PLUGMAPOBJ" in rows[0]["message"]
    assert attrs["last_run_n_failed"] == 2 and attrs["last_run_exit_status"] == 7
    assert attrs["last_run_exit_status_name"] == "DATABASE_ERROR"
    assert attrs["last_run_n_exception"] == 1 and attrs["last_run_n_database_error"] == 1

    # Retrying with --skip-existing: the night that now works is removed from
    # the table, the one that still fails stays, and the run still exits 3.
    fake_nights[("apo", MJD + 2)] = "no_data"
    result = run_cli(["--mjd-start", str(MJD), "--mjd-end", str(MJD + 2), "--apo", "-O", out,
                      "-p", "1", "--skip-existing"])
    assert result.exit_code == ExitStatus.NIGHT_EXCEPTION
    with h5.File(out, "r") as fp:
        rows = io.read_run_summary(fp)
        attrs = dict(fp["run_summary"].attrs)
    assert [(r["mjd"], r["category"]) for r in rows] == [(MJD + 1, "exception")]
    assert attrs["last_run_n_skipped_existing"] == 1


def test_db_unavailable_exit_3_or_4(tmp_path, fake_nights):
    fake_nights[("apo", MJD)] = "db_unavailable"
    out = str(tmp_path / "a.h5")
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1", "--fibers"])
    assert result.exit_code == ExitStatus.DB_UNAVAILABLE_CROSS_MATCH
    with h5.File(out, "r") as fp:
        assert [(r["mjd"], r["category"]) for r in io.read_run_summary(fp)] == [(MJD, "db_unavailable")]
        assert f"raw/apo/{MJD}/exposures" in fp

    out2 = str(tmp_path / "b.h5")
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out2, "-p", "1"])
    assert result.exit_code == ExitStatus.DB_UNAVAILABLE
    with h5.File(out2, "r") as fp:
        assert fp["run_summary"].attrs["last_run_n_db_unavailable"] == 1
        assert fp["run_summary"].attrs["last_run_exit_status_name"] == "DB_UNAVAILABLE"


def test_skip_existing_is_success(tmp_path, fake_nights):
    out = str(tmp_path / "a.h5")
    fake_nights[("apo", MJD)] = "data"
    assert run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1"]).exit_code == 0
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1", "--skip-existing"])
    assert result.exit_code == 0


def test_cross_match_without_database_raises_clear_error(monkeypatch):
    import almanac.database as database
    from almanac.data_models.fps import FPSTarget

    monkeypatch.setattr(database, "is_database_available", False)
    exposures = make_exposures()
    target = FPSTarget(category="science", racat=1.0, deccat=2.0, fiberId=1, catalogid=4204682060)
    for e in exposures[:2]:
        e._targets = (target,)
    with pytest.raises(apogee.DatabaseUnavailableError):
        apogee.cross_match_targets(exposures, [(1, 2)], xmatch=True)


def test_write_failed_exit_8(tmp_path, fake_nights, monkeypatch):
    fake_nights[("apo", MJD)] = "data"

    def broken_update(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(io, "update", broken_update)
    out = str(tmp_path / "a.h5")
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1"])
    assert result.exit_code == ExitStatus.WRITE_FAILED
    with h5.File(out, "r") as fp:
        assert [r["category"] for r in io.read_run_summary(fp)] == ["write_failed"]


def _night_through_process_night(observatory, mjd, fibers=False, meta=False, **kwargs):
    """`get_almanac_data` with the real night path, on the helper exposures."""
    return apogee.process_night(
        observatory, mjd, make_exposures(observatory, mjd), (), fibers=fibers, meta=meta, **kwargs
    )


@pytest.mark.parametrize("processes", [1, 2])
def test_missing_confsummary_is_night_exception_not_write_failed(
    tmp_path, fake_nights, monkeypatch, processes
):
    # A configuration without a confSummary file: the targets are loaded in
    # the night pipeline (worker side), so the night fails there as an
    # exception (6), never in the writer as WRITE_FAILED (8), which stops
    # whole builds and is reserved for output I/O errors.
    from almanac.config import config

    sdsscore = tmp_path / "sdsscore"
    sdsscore.mkdir()
    monkeypatch.setattr(config, "sdsscore_dir", str(sdsscore))
    monkeypatch.setattr(apogee, "get_almanac_data", _night_through_process_night)
    monkeypatch.setattr("almanac.cli._pool_initializer", lambda *a, **k: None)
    writes = []
    real_update = io.update
    monkeypatch.setattr(io, "update", lambda *a, **k: (writes.append(a[1:3]), real_update(*a, **k)))
    out = str(tmp_path / "a.h5")
    mjds = f"{MJD},{MJD + 1}" if processes > 1 else str(MJD)
    result = run_cli(["--mjds", mjds, "--apo", "-O", out, "-p", str(processes), "--fibers"])
    assert result.exit_code == ExitStatus.NIGHT_EXCEPTION
    with h5.File(out, "r") as fp:
        rows = io.read_run_summary(fp)
        assert f"raw/apo/{MJD}" not in fp
    assert {(r["mjd"], r["category"]) for r in rows} == {
        (m, "exception") for m in map(int, mjds.split(","))
    }
    assert all("Could not find confSummary file for config 123" in r["message"] for r in rows)
    if processes == 1:
        assert writes == []  # the writer was never reached


def _die_in_worker(observatory, mjd, *args, **kwargs):
    import os
    os._exit(1)  # a worker killed mid-night


def test_pool_broken_exit_9(tmp_path, fake_nights, monkeypatch):
    monkeypatch.setattr(apogee, "get_almanac_data", _die_in_worker)
    monkeypatch.setattr("almanac.cli._pool_initializer", lambda *a, **k: None)
    out = str(tmp_path / "a.h5")
    result = run_cli(["--mjds", f"{MJD},{MJD + 1}", "--apo", "-O", out, "-p", "2"])
    assert result.exit_code == ExitStatus.POOL_BROKEN
    with h5.File(out, "r") as fp:
        assert {r["category"] for r in io.read_run_summary(fp)} == {"pool_broken"}


def test_add_metadata_batch_failure_is_database_error():
    from almanac.metadata import MetadataBuildError, _run_batches

    def failing(ids, suffix=""):
        raise RuntimeError("connection reset")

    with pytest.raises(MetadataBuildError) as info:
        _run_batches([(0, [1, 2])], failing, 0, lambda *a: None, 1, 0.0, 0.0)
    assert info.value.exit_status == ExitStatus.DATABASE_ERROR
