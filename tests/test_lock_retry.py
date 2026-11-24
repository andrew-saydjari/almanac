"""Retrying the open of the output while its HDF5 lock is briefly taken (Ceph)."""

import errno

import h5py
import pytest

import almanac.apogee as apogee
from almanac import io
from almanac.exit_codes import ExitStatus
from tests.helpers import SEQUENCES, make_exposures, run_cli

MJD = 60000


def lock_error():
    return BlockingIOError(errno.EAGAIN, "Unable to synchronously open file (unable to lock file, "
                           "errno = 11, error message = 'Resource temporarily unavailable')")


def test_open_retries_then_succeeds():
    calls, sleeps = [], []

    def fake_open(path, mode):
        calls.append(mode)
        if len(calls) <= 3:
            raise lock_error()
        return "handle"

    assert io.open_h5_retrying("x.h5", "a", _open=fake_open, _sleep=sleeps.append) == "handle"
    assert len(calls) == 4 and sleeps == [0.1, 0.2, 0.4]


def test_open_gives_up_after_budget_and_other_errors_are_immediate():
    sleeps = []

    def locked(path, mode):
        raise lock_error()

    with pytest.raises(OSError, match="unable to lock file"):
        io.open_h5_retrying("x.h5", "a", budget=60, _open=locked, _sleep=sleeps.append)
    assert sum(sleeps) <= 60 and max(sleeps) <= io.LOCK_RETRY_MAX_DELAY and len(sleeps) > 5

    def missing(path, mode):
        raise FileNotFoundError(errno.ENOENT, "no such file")

    sleeps.clear()
    with pytest.raises(FileNotFoundError):
        io.open_h5_retrying("x.h5", "a", _open=missing, _sleep=sleeps.append)
    assert sleeps == []


def test_second_writer_still_refused(tmp_path):
    """A real second writer (another process) holds the lock longer than the budget."""
    import os
    import subprocess
    import sys
    import time

    path = str(tmp_path / "a.h5")
    h5py.File(path, "w").close()
    env = {k: v for k, v in os.environ.items() if k != "HDF5_USE_FILE_LOCKING"}
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import h5py, time; f = h5py.File({path!r}, 'a'); print('open', flush=True); time.sleep(20)"],
        stdout=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert holder.stdout.readline().strip() == "open"
        t0 = time.time()
        with pytest.raises(OSError, match="lock"):
            io.open_h5_retrying(path, "a", budget=0.5)
        assert time.time() - t0 < 5
    finally:
        holder.kill()
        holder.wait()


@pytest.fixture
def one_night(monkeypatch):
    def fake(observatory, mjd, fibers=False, meta=False, **kwargs):
        return observatory, mjd, make_exposures(observatory, mjd), dict(SEQUENCES), []

    monkeypatch.setattr(apogee, "get_almanac_data", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)


def _failing_h5_file(monkeypatch, out, fail):
    """io.h5.File fails with a lock error for the opens of `out` numbered in `fail`."""
    real, n = h5py.File, [0]

    def wrapper(path, mode="r", *args, **kwargs):
        if str(path) == out and mode == "a":
            n[0] += 1
            if fail(n[0]):
                raise lock_error()
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(io.h5, "File", wrapper)


def test_night_written_after_lock_retries(tmp_path, monkeypatch, one_night):
    out = str(tmp_path / "a.h5")
    io.lock_retry_count = 0
    _failing_h5_file(monkeypatch, out, lambda k: k in (2, 3))  # the night's open, twice
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1"])
    assert result.exit_code == 0
    with h5py.File(out, "r") as fp:
        assert f"raw/apo/{MJD}/exposures" in fp
        assert fp["run_summary"].attrs["last_run_n_lock_retries"] == 2


def test_lock_never_released_is_write_failed(tmp_path, monkeypatch, one_night):
    out = str(tmp_path / "a.h5")
    monkeypatch.setattr(io, "LOCK_RETRY_BUDGET", 1.0)
    calls = []
    # The first open (resume check) and the final one succeed; the night's never does.
    _failing_h5_file(monkeypatch, out, lambda k: calls.append(k) or 2 <= k <= 6)
    result = run_cli(["--mjd", str(MJD), "--apo", "-O", out, "-p", "1"])
    assert result.exit_code == ExitStatus.WRITE_FAILED
    with h5py.File(out, "r") as fp:
        assert f"raw/apo/{MJD}" not in fp
        assert [r["category"] for r in io.read_run_summary(fp)] == ["write_failed"]
