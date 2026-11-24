"""End-of-run summary and exit status of the main command (bug A)."""

import h5py as h5
import pytest
from click.testing import CliRunner
from peewee import OperationalError

import almanac.apogee as apogee
from almanac import io
from almanac.cli import (
    EXIT_DB_UNAVAILABLE,
    EXIT_NIGHTS_FAILED,
    _failure_category,
    _run_exit_status,
    main,
)
from tests.helpers import SEQUENCES, make_exposures

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
        return observatory, mjd, make_exposures(observatory, mjd), dict(SEQUENCES), missing

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
    assert _run_exit_status(failed, set(), False) == EXIT_NIGHTS_FAILED
    assert _run_exit_status(failed, {("apo", 2)}, True) == EXIT_NIGHTS_FAILED
    assert _run_exit_status([], {("apo", 2)}, True) == EXIT_DB_UNAVAILABLE
    # Without the cross-match, a night built without the operations database
    # keeps exit status 0 (judgment call, unchanged).
    assert _run_exit_status([], {("apo", 2)}, False) == 0


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
    assert attrs["last_run_exit_status"] == 0


def test_failed_nights_exit_3_and_are_recorded(tmp_path, fake_nights):
    out = str(tmp_path / "a.h5")
    fake_nights[("apo", MJD)] = "data"
    fake_nights[("apo", MJD + 1)] = "plugmap"
    fake_nights[("apo", MJD + 2)] = "database"
    result = run_cli(["--mjd-start", str(MJD), "--mjd-end", str(MJD + 2), "--apo", "-O", out, "-p", "1"])
    assert result.exit_code == EXIT_NIGHTS_FAILED
    with h5.File(out, "r") as fp:
        rows = io.read_run_summary(fp)
        attrs = dict(fp["run_summary"].attrs)
        assert f"raw/apo/{MJD}/exposures" in fp
    assert [(r["mjd"], r["category"]) for r in rows] == [
        (MJD + 1, "exception"), (MJD + 2, "database_error")
    ]
    assert "PLUGMAPOBJ" in rows[0]["message"]
    assert attrs["last_run_n_failed"] == 2 and attrs["last_run_exit_status"] == 3

    # Retrying with --skip-existing: the night that now works is removed from
    # the table, the one that still fails stays, and the run still exits 3.
    fake_nights[("apo", MJD + 2)] = "no_data"
    result = run_cli(["--mjd-start", str(MJD), "--mjd-end", str(MJD + 2), "--apo", "-O", out,
                      "-p", "1", "--skip-existing"])
    assert result.exit_code == EXIT_NIGHTS_FAILED
    with h5.File(out, "r") as fp:
        rows = io.read_run_summary(fp)
        attrs = dict(fp["run_summary"].attrs)
    assert [(r["mjd"], r["category"]) for r in rows] == [(MJD + 1, "exception")]
    assert attrs["last_run_n_skipped_existing"] == 1


def test_db_unavailable_exit_4_only_with_cross_match(tmp_path, fake_nights):
    fake_nights[("apo", MJD)] = "db_unavailable"
    out = str(tmp_path / "a.h5")
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1", "--fibers"])
    assert result.exit_code == EXIT_DB_UNAVAILABLE
    with h5.File(out, "r") as fp:
        assert [(r["mjd"], r["category"]) for r in io.read_run_summary(fp)] == [(MJD, "db_unavailable")]
        assert f"raw/apo/{MJD}/exposures" in fp

    out2 = str(tmp_path / "b.h5")
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out2, "-p", "1"])
    assert result.exit_code == 0
    with h5.File(out2, "r") as fp:
        assert fp["run_summary"].attrs["last_run_n_db_unavailable"] == 1


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
