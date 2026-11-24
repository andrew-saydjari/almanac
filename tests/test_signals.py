"""Clean stops on signals, per-night flushes, resume after a kill (bug B)."""

import os
import signal
import subprocess
import sys
import textwrap

import h5py as h5
import pytest

from almanac import io
from almanac.signals import GracefulStop, StopRequested
from tests.helpers import SEQUENCES, write_night

MJD = 60000

# Runs the main command in a child process with the per-night pipeline
# replaced: three nights with data; while processing the night given by
# argv[2], the process sends itself the signal named by argv[3].
RUNNER = textwrap.dedent('''
    import os, signal, sys
    sys.path.insert(0, {repo!r})
    import almanac.apogee as apogee
    from tests.helpers import SEQUENCES, make_exposures

    output, stop_mjd, signame = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    def fake(observatory, mjd, fibers=False, meta=False, **kwargs):
        if mjd == stop_mjd:
            os.kill(os.getpid(), getattr(signal, signame))
        return observatory, mjd, make_exposures(observatory, mjd), dict(SEQUENCES), [
            dict(observatory=observatory, mjd=mjd, exposure=9, expected_max_db=9, reason="trailing")
        ]

    apogee.get_almanac_data = fake
    from almanac.cli import main
    main(["--mjd-start", "{mjd}", "--mjd-end", "{mjd2}", "--apo", "-O", output, "-p", "1"]
         + sys.argv[4:])
''')


def run_child(tmp_path, stop_mjd, signame, extra=(), ignore_sigint=False):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "runner.py"
    script.write_text(RUNNER.format(repo=repo, mjd=MJD, mjd2=MJD + 2))
    output = str(tmp_path / "out.h5")

    def preexec():
        if ignore_sigint:  # as for a job started with '&' by a non-interactive shell
            signal.signal(signal.SIGINT, signal.SIG_IGN)

    proc = subprocess.run(
        [sys.executable, str(script), output, str(stop_mjd), signame, *extra],
        capture_output=True, text=True, timeout=300, preexec_fn=preexec, cwd=repo,
    )
    return proc, output


def nights_in(output):
    with h5.File(output, "r") as fp:
        return sorted(int(m) for m in fp.get("raw/apo", {}))


@pytest.mark.parametrize("signame, status", [("SIGTERM", 143), ("SIGHUP", 129), ("SIGINT", 130)])
def test_signal_stops_cleanly(tmp_path, signame, status):
    proc, output = run_child(tmp_path, MJD + 1, signame)
    assert proc.returncode == status, proc.stderr[-2000:]
    # The file opens; the night written before the signal is in it with its
    # missing-exposure rows; the night being processed and the rest are not.
    assert nights_in(output) == [MJD]
    with h5.File(output, "r") as fp:
        assert [int(m) for m in fp["missing_exposures/mjd"][:]] == [MJD]
        attrs = dict(fp["run_summary"].attrs)
        assert io.PENDING_MISSING_DATASET not in fp
    assert attrs["last_run_exit_status"] == status
    assert attrs["last_run_n_with_data"] == 1 and attrs["last_run_n_not_processed"] == 2

    # Resume: the remaining nights are processed, the first is skipped.
    proc, output = run_child(tmp_path, -1, "SIGTERM", ["--skip-existing"])
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert nights_in(output) == [MJD, MJD + 1, MJD + 2]
    with h5.File(output, "r") as fp:
        assert sorted(int(m) for m in fp["missing_exposures/mjd"][:]) == [MJD, MJD + 1, MJD + 2]


def test_sigint_handled_in_background_job(tmp_path):
    """A job started with SIGINT ignored still stops cleanly on SIGINT."""
    proc, output = run_child(tmp_path, MJD + 1, "SIGINT", ignore_sigint=True)
    assert proc.returncode == 130, proc.stderr[-2000:]
    assert nights_in(output) == [MJD]


def test_pending_nights_are_reprocessed_on_resume(tmp_path):
    """A night written by a run that was then killed (its missing-exposure rows
    never recorded) is processed again with --skip-existing, not skipped."""
    output = str(tmp_path / "out.h5")
    for mjd in (MJD, MJD + 1):
        write_night(output, mjd=mjd)
    with h5.File(output, "a") as fp:
        io.add_pending_missing(fp, "apo", MJD + 1)
        assert io.read_pending_missing(fp) == {("apo", MJD + 1)}
    proc, output = run_child(tmp_path, -1, "SIGTERM", ["--skip-existing"])
    assert proc.returncode == 0, proc.stderr[-2000:]
    with h5.File(output, "r") as fp:
        # MJD was skipped (no rows), MJD + 1 and MJD + 2 processed.
        assert sorted(int(m) for m in fp["missing_exposures/mjd"][:]) == [MJD + 1, MJD + 2]
        assert io.PENDING_MISSING_DATASET not in fp
        assert fp["run_summary"].attrs["last_run_n_skipped_existing"] == 1


def test_open_for_writing_disables_metadata_evictions(tmp_path):
    path = str(tmp_path / "a.h5")
    with io.open_for_writing(path) as fp:
        assert not fp.id.get_mdc_config().evictions_enabled
        fp.create_group("x")
    with h5.File(path, "r") as fp:
        assert "x" in fp


def test_graceful_stop_handlers():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    signal.signal(signal.SIGHUP, signal.SIG_IGN)  # as under nohup
    try:
        stop = GracefulStop()
        with stop:
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN  # nohup respected
            with pytest.raises(StopRequested) as info:
                os.kill(os.getpid(), signal.SIGTERM)
                signal.sigtimedwait([], 0)  # let the handler run
                for _ in range(1000):
                    pass
            assert info.value.signum == signal.SIGTERM and stop.exit_status == 143
            with stop.deferred():
                os.kill(os.getpid(), signal.SIGTERM)  # recorded, not raised
        assert signal.getsignal(signal.SIGTERM) == previous[signal.SIGTERM]
    finally:
        for s, h in previous.items():
            signal.signal(s, h)


def test_worker_signals():
    code = textwrap.dedent('''
        import ctypes, signal
        from almanac.signals import worker_signals
        worker_signals()
        value = ctypes.c_int()
        ctypes.CDLL(None).prctl(2, ctypes.byref(value), 0, 0, 0)  # PR_GET_PDEATHSIG
        print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN, value.value)
    ''')
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    ignored, deathsig = out.stdout.split()
    assert ignored == "True"
    if sys.platform.startswith("linux"):
        assert int(deathsig) == signal.SIGKILL


ADD_FIBERS_RUNNER = textwrap.dedent('''
    import os, signal, sys
    sys.path.insert(0, {repo!r})
    import almanac.apogee as apogee
    from tests.test_add_fibers import _fake_process_night

    path, stop_mjd = sys.argv[1], int(sys.argv[2])

    def fake(observatory, mjd, *args, **kwargs):
        if mjd == stop_mjd:
            os.kill(os.getpid(), signal.SIGTERM)
        return _fake_process_night(observatory, mjd, *args, **kwargs)

    apogee.process_night = fake
    from almanac.cli import main
    main(["add", "fibers", path, "--in-place", "--no-x-match", "-p", "1"])
''')


def test_add_fibers_sigterm_keeps_written_nights(tmp_path):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "runner.py"
    script.write_text(ADD_FIBERS_RUNNER.format(repo=repo))
    path = str(tmp_path / "night.h5")
    for mjd in (MJD, MJD + 1, MJD + 2):
        write_night(path, mjd=mjd)
    proc = subprocess.run([sys.executable, str(script), path, str(MJD + 1)],
                          capture_output=True, text=True, timeout=300, cwd=repo)
    assert proc.returncode == 143, proc.stderr[-2000:]
    with h5.File(path, "r") as fp:
        assert [m for m in (MJD, MJD + 1, MJD + 2) if f"raw/apo/{m}/fibers" in fp] == [MJD]
