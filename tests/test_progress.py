"""Live progress bars (`almanac.progress.TaskDisplay`).

The bars are drawn only when standard output and standard error are
terminals, never into a log file, and can be turned off. Here: the decision
rule, the counters (resumed count, retries never move a bar back), the
non-terminal output (no escape sequences or carriage returns, the log lines
unchanged) and, in a pseudo-terminal, the bars themselves, worker output
printed above them and a clean stop on SIGTERM.
"""

import json
import os
import re
import select
import signal
import subprocess
import sys
import time

import h5py as h5
import pytest

import almanac.metadata as md
from almanac.progress import NO_PROGRESS_ENV, TaskDisplay, TaskStatus, progress_enabled
from tests.helpers import run_cli
from tests.test_metadata_build import Recorder, fake_query, make_almanac

DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress_driver.py")
ESC_OR_CR = re.compile(r"[\r\x1b]")


class _Stream:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


TTY, PIPE = _Stream(True), _Stream(False)


class TestProgressEnabled:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.delenv(NO_PROGRESS_ENV, raising=False)
        monkeypatch.setenv("TERM", "xterm")

    def test_terminal(self):
        assert progress_enabled(streams=(TTY, TTY))

    @pytest.mark.parametrize("streams", [(PIPE, TTY), (TTY, PIPE), (PIPE, PIPE), (TTY, None)])
    def test_not_a_terminal(self, streams):
        assert not progress_enabled(streams=streams)

    def test_no_progress_flag(self):
        assert not progress_enabled(True, streams=(TTY, TTY))

    @pytest.mark.parametrize("value, enabled", [("1", False), ("yes", False), ("0", True), ("", True)])
    def test_environment_variable(self, monkeypatch, value, enabled):
        monkeypatch.setenv(NO_PROGRESS_ENV, value)
        assert progress_enabled(streams=(TTY, TTY)) is enabled

    def test_dumb_terminal(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        assert not progress_enabled(streams=(TTY, TTY))

    def test_pytest_capture_is_not_a_terminal(self):
        assert not progress_enabled()
        assert not TaskDisplay().enabled


class TestTaskDisplay:
    def test_disabled_display_draws_nothing(self, capsys):
        bars = TaskDisplay(enabled=False)
        with bars:
            t = bars.add_task("Querying catalog", total=10, unit="ids")
            bars.advance(t, 4)
        assert bars.tasks[t].completed == 4
        out = capsys.readouterr()
        assert out.out == "" and out.err == ""

    def test_initial_counts_as_done_but_not_in_the_rate(self):
        bars = TaskDisplay(enabled=False)
        t = bars.add_task("Querying catalog", total=100, unit="ids", initial=60)
        task = bars.tasks[t]
        assert task.completed == 60
        bars.start_task(t)
        task.started -= 10.0  # 10 s ago
        bars.advance(t, 20)
        rate, elapsed, eta = TaskDisplay.rate_and_eta(task)
        assert rate == pytest.approx(2.0, rel=0.05)  # 20 ids in 10 s, not 80
        assert eta == pytest.approx(10.0, rel=0.05)  # 20 ids left

    def test_set_initial_and_set_completed_never_go_back(self):
        bars = TaskDisplay(enabled=False)
        t = bars.add_task("Processing nights", total=10, unit="nights")
        bars.set_initial(t, 3)
        assert bars.tasks[t].completed == 3
        bars.set_completed(t, 5)
        bars.set_completed(t, 2)
        assert bars.tasks[t].completed == 5

    def test_more_work_reopens_a_completed_task(self):
        bars = TaskDisplay(enabled=False)
        t = bars.add_task("Writing nights", total=1, unit="nights")
        bars.advance(t)
        assert bars.tasks[t].status == TaskStatus.COMPLETED
        bars.update_task(t, total=2)
        assert bars.tasks[t].status == TaskStatus.RUNNING

    def test_render_shows_units_rate_and_eta(self):
        from rich.console import Console

        bars = TaskDisplay(enabled=False)
        t = bars.add_task("Querying catalog", total=1000, unit="ids", initial=200)
        bars.start_task(t)
        bars.tasks[t].started -= 4.0
        bars.advance(t, 400)
        bars.update_task(t, note="6/10 batches")
        console = Console(width=160, record=True, file=open(os.devnull, "w"))
        console.print(bars.renderable())
        text = console.export_text()
        assert "Querying catalog" in text and "(6/10 batches)" in text
        assert "600/1,000 ids" in text
        assert "ids/s" in text and "ETA 0:00:0" in text

    def test_bytes(self):
        from rich.console import Console

        bars = TaskDisplay(enabled=False)
        t = bars.add_task("Copying a.h5", total=3 * 1024**3, as_bytes=True)
        bars.advance(t, 1024**3)
        console = Console(width=160, record=True, file=open(os.devnull, "w"))
        console.print(bars.renderable())
        assert "1.0 GiB/3.0 GiB" in console.export_text()


class _Recording(TaskDisplay):
    """A disabled display that records every (task name, completed, note) change."""

    def __init__(self):
        super().__init__(enabled=False)
        self.history = []

    def _record(self, task_id):
        task = self.tasks[task_id]
        self.history.append((task.name, task.completed, task.note))

    def advance(self, task_id, amount=1):
        super().advance(task_id, amount)
        self._record(task_id)

    def update_task(self, task_id, **kwargs):
        super().update_task(task_id, **kwargs)
        self._record(task_id)


class TestMetadataBars:
    def test_bars_count_ids_and_finish(self, tmp_path):
        path = make_almanac(str(tmp_path / "almanac.h5"))
        bars = _Recording()
        md.build_metadata(path, output_path=str(tmp_path / "t.h5"), query_batch=fake_query,
                          query_workers=0, batch_size=10, progress=bars)
        by_name = {t.name: t for t in bars.tasks.values()}
        assert by_name["Reading fiber mappings"].completed == 2
        assert by_name["Reading fiber mappings"].unit == "nights"
        q = by_name["Querying catalog"]
        assert (q.unit, q.completed, q.total, q.initial) == ("ids", 60, 60, 0)
        assert q.status == TaskStatus.COMPLETED
        assert by_name["Assembling /meta"].completed == 6
        ids = [c for n, c, _ in bars.history if n == "Querying catalog"]
        assert ids == sorted(ids) and ids[-1] == 60

    def test_resume_starts_from_the_checkpoint(self, tmp_path):
        path = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "t.h5")
        # First run: batches 0-2 finish, batch 3 fails for good (checkpoint kept).
        with pytest.raises(md.MetadataBuildError):
            md.build_metadata(path, output_path=out, query_workers=0, batch_size=10,
                              query_batch=Recorder(fail_on_batch_call=3, fail_exc=ConnectionError("x")),
                              max_batch_attempts=1, retry_backoff=0.0)
        progress = json.load(open(f"{out}.meta-checkpoint/progress.json"))
        assert progress["ids_done"] == 30
        bars = _Recording()
        md.build_metadata(path, output_path=out, query_workers=0, batch_size=10,
                          query_batch=fake_query, progress=bars)
        q = next(t for t in bars.tasks.values() if t.name == "Querying catalog")
        assert (q.initial, q.completed, q.total) == (30, 60, 60)
        ids = [c for n, c, _ in bars.history if n == "Querying catalog"]
        assert ids[0] == 30 and ids == sorted(ids)

    def test_retries_show_in_the_note_and_never_move_the_bar_back(self, tmp_path):
        path = make_almanac(str(tmp_path / "almanac.h5"))
        bars = _Recording()
        md.build_metadata(path, output_path=str(tmp_path / "t.h5"), query_workers=0, batch_size=10,
                          query_batch=Recorder(fail_on_batch_call=2, fail_exc=ConnectionError("x"),
                                               fail_times=2),
                          max_batch_attempts=3, retry_backoff=0.0, progress=bars)
        history = [(c, note) for n, c, note in bars.history if n == "Querying catalog"]
        counts = [c for c, _ in history]
        assert counts == sorted(counts) and counts[-1] == 60
        notes = " ".join(note for _, note in history)
        assert "failed, attempt 1/3" in notes and "to retry" in notes


class TestNonTerminalOutput:
    """CliRunner's output is not a terminal: no bars, and the output is the
    same with and without --no-progress."""

    def test_add_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "_default_query_batch", fake_query)
        outputs = []
        for flag in ([], ["--no-progress"]):
            path = make_almanac(str(tmp_path / f"almanac{len(outputs)}.h5"))
            result = run_cli(["add", "metadata", path, "--query-workers", "0",
                              "--batch-size", "10", "--output", str(tmp_path / f"t{len(outputs)}.h5"),
                              *flag])
            assert result.exit_code == 0, result.output
            assert not ESC_OR_CR.search(result.output)
            assert "Querying catalog" not in result.output
            assert "[add metadata] batch 5 done" in result.output
            outputs.append(_normalise(result.output.replace(f"almanac{len(outputs)}", "almanacN")
                                      .replace(f"t{len(outputs)}.h5", "tN.h5")))
        assert outputs[0] == outputs[1]

    def test_main_build(self, tmp_path):
        outputs = []
        for flag in ([], ["--no-progress"]):
            result = run_cli(["--mjd", "51000", "--apo", "-p", "0", "--output",
                              str(tmp_path / f"out{len(outputs)}.h5"), *flag])
            assert result.exit_code == 0
            assert not ESC_OR_CR.search(result.output)
            outputs.append(result.output)
        assert outputs[0] == outputs[1]


def _normalise(text):
    text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?\+00:00", "TS", text)
    return re.sub(r"[\d.]+ ids/s recent", "R ids/s recent", text)


# --------------------------------------------------------------------------- #
# In a separate process: a pseudo-terminal, or a pipe as with `>> log 2>&1`
# --------------------------------------------------------------------------- #


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k != NO_PROGRESS_ENV}
    env.update(TERM="xterm-256color", COLUMNS="150", LINES="40", PYTHONUNBUFFERED="1")
    env.update(extra)
    return env


def run_in_pty(args, env=None, timeout=120, on_output=None):
    """Run the driver with a pseudo-terminal as stdin/stdout/stderr."""
    import pty

    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, DRIVER, *args], stdin=slave, stdout=slave,
                            stderr=slave, env=env or _env(), close_fds=True)
    os.close(slave)
    out = b""
    deadline = time.time() + timeout
    try:
        while True:
            if time.time() > deadline:
                proc.kill()
                pytest.fail(f"driver timed out; output so far:\n{out.decode(errors='replace')}")
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO: every writer has closed the terminal
                    break
                if not data:
                    break
                out += data
                if on_output is not None:
                    on_output(proc, out)
            elif proc.poll() is not None:
                break
    finally:
        os.close(master)
    return proc.wait(timeout=30), out.decode(errors="replace")


def run_with_pipe(args, env=None, timeout=120):
    """Run the driver as `... >> log 2>&1 < /dev/null` would."""
    proc = subprocess.run([sys.executable, DRIVER, *args], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env or _env(), timeout=timeout)
    return proc.returncode, proc.stdout.decode(errors="replace")


BAR_SEQUENCES = ("\r", "\x1b[?25l", "\x1b[2K", "\x1b[1A")


class TestPseudoTerminal:
    def test_metadata_bars_drawn_with_logs_and_worker_output_above(self, tmp_path):
        rc, out = run_in_pty(["meta", str(tmp_path), "2", "0.1"])
        assert rc == 0, out
        assert "Querying catalog" in out and "60/60 ids" in out and "ids/s" in out
        assert "Reading fiber mappings" in out and "Assembling /meta" in out
        assert "[add metadata] batch 5 done" in out or "[add metadata] batch" in out
        # A forked query worker's print and log record reach the terminal.
        assert "says hello from batch_1" in out
        assert "worker log line from batch_1" in out
        # The cursor is shown again at the end.
        assert out.rfind("\x1b[?25h") > out.rfind("\x1b[?25l") >= 0

    def test_off_switch_in_a_terminal(self, tmp_path):
        rc, out = run_in_pty(["meta", str(tmp_path), "0", "0"], env=_env(**{NO_PROGRESS_ENV: "1"}))
        assert rc == 0, out
        assert "Querying catalog" not in out
        assert "\x1b[?25l" not in out
        assert "[add metadata] batch 5 done" in out

    def test_main_build_bars(self, tmp_path):
        rc, out = run_in_pty(["build", str(tmp_path), "0.2"])
        assert rc == 0, out
        assert "Processing nights" in out and "3/3 nights" in out
        assert "Run summary: requested 3" in out
        assert out.rfind("\x1b[?25h") > out.rfind("\x1b[?25l") >= 0

    def test_main_build_no_progress_flag(self, tmp_path):
        rc, out = run_in_pty(["build", str(tmp_path), "0", "--no-progress"])
        assert rc == 0, out
        assert "Processing nights" not in out and "\x1b[?25l" not in out

    def test_metadata_sigterm_leaves_terminal_and_checkpoint(self, tmp_path):
        sent = []

        def stop_after_first_batch(proc, out):
            if not sent and b"batch 1 done" in out:
                os.kill(proc.pid, signal.SIGTERM)
                sent.append(True)

        rc, out = run_in_pty(["meta", str(tmp_path), "0", "0.3"], on_output=stop_after_first_batch)
        assert sent and rc == 143, out
        assert "terminated; finished batches are kept" in out
        assert out.rfind("\x1b[?25h") > out.rfind("\x1b[?25l") >= 0
        ckpt = tmp_path / "targets.h5.meta-checkpoint"
        progress = json.load(open(ckpt / "progress.json"))
        assert progress["ids_done"] >= 20
        assert len(list(ckpt.glob("batch_*.h5"))) == progress["batches_done"]

    def test_main_build_sigterm(self, tmp_path):
        sent = []

        def stop_once_running(proc, out):
            if not sent and b"Processing nights" in out:
                time.sleep(0.3)
                os.kill(proc.pid, signal.SIGTERM)
                sent.append(True)

        rc, out = run_in_pty(["build", str(tmp_path), "1.0"], on_output=stop_once_running)
        assert sent and rc == 128 + signal.SIGTERM, out
        assert "Stopped by signal" in out and "Run summary" in out
        assert out.rfind("\x1b[?25h") > out.rfind("\x1b[?25l") >= 0
        with h5.File(tmp_path / "out.h5", "r") as fp:
            assert int(fp["run_summary"].attrs["last_run_exit_status"]) == 143


class TestPipe:
    """As under `setsid nohup ... >> log 2>&1 < /dev/null`: no bar output at all."""

    def test_metadata(self, tmp_path):
        rc, out = run_with_pipe(["meta", str(tmp_path), "2", "0.05"])
        assert rc == 0, out
        assert "Querying catalog" not in out
        for seq in BAR_SEQUENCES:
            assert seq not in out
        lines = [line for line in out.splitlines() if "[add metadata]" in line]
        assert sum("] batch " in line and " done (" in line for line in lines) == 6
        assert all(line.startswith("[") for line in lines)

    def test_main_build_verbose(self, tmp_path):
        rc, out = run_with_pipe(["build", str(tmp_path), "0.05", "-v"])
        assert rc == 0, out
        assert "Processing nights" not in out
        for seq in BAR_SEQUENCES:
            assert seq not in out
        assert "Run summary: requested 3" in out
