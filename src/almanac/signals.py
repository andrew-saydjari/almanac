"""
Clean stops on signals, for the commands that write almanac files.

`GracefulStop` turns SIGINT, SIGTERM (what ``kill PID`` and a Slurm time
limit send) and SIGHUP into a `StopRequested` exception (a
`KeyboardInterrupt`) raised in the main thread, so the command can kill its
workers, finish the HDF5 write in progress and close the file. SIGINT is
handled even when the process started with SIGINT ignored (a job started with
``&`` from a non-interactive shell); SIGHUP is left alone when it is ignored
(``nohup``). A second signal, or one that arrives while the command is
already finishing (`GracefulStop.deferred`), does not interrupt the clean-up.

SIGKILL cannot be handled: the commands therefore flush and close their
output after every night (see `almanac.io.open_for_writing`).

`worker_signals` is called in every pool worker: the workers leave stopping
to the parent (SIGINT ignored, SIGTERM/SIGHUP default) and, on Linux, are
killed when the parent dies, so a parent killed outright leaves no orphans.
"""

import signal
import threading
from contextlib import contextmanager

__all__ = ["StopRequested", "GracefulStop", "worker_signals"]

_SIGNAL_NAMES = ("SIGINT", "SIGTERM", "SIGHUP")


class StopRequested(KeyboardInterrupt):
    """A stop was requested by a signal (`signum`)."""

    def __init__(self, signum: int):
        super().__init__(f"stopped by signal {signum}")
        self.signum = signum


class GracefulStop:
    """
    Context manager that installs the handlers (main thread only; elsewhere a
    no-op) and restores the previous ones on exit.

    ``signum`` is the first signal received (None if none).
    """

    def __init__(self):
        self.signum = None
        self._deferred = False
        self._previous = {}

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            return self
        for name in _SIGNAL_NAMES:
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            previous = signal.getsignal(signum)
            if name == "SIGHUP" and previous == signal.SIG_IGN:
                continue  # nohup: the user asked to survive a hangup
            self._previous[signum] = previous
            signal.signal(signum, self._handle)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for signum, previous in self._previous.items():
            signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
        self._previous = {}
        return False

    def _handle(self, signum, frame):
        if self.signum is not None or self._deferred:
            if self.signum is None:
                self.signum = signum
            from almanac import logger

            logger.warning(f"Received signal {signum}; finishing the clean stop first")
            return
        self.signum = signum
        raise StopRequested(signum)

    @contextmanager
    def deferred(self):
        """Within this block a signal is recorded, not raised."""
        self._deferred = True
        try:
            yield self
        finally:
            self._deferred = False

    @property
    def exit_status(self) -> int:
        """128 + the signal number (the shell convention), or 0."""
        return 128 + int(self.signum) if self.signum is not None else 0


def worker_signals() -> None:
    """Signal set-up for a pool worker process (see the module docstring)."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
    except ValueError:  # not the main thread of the worker
        pass
    try:
        import ctypes
        import ctypes.util
        import sys

        if sys.platform.startswith("linux"):
            PR_SET_PDEATHSIG = 1
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)
    except Exception:
        pass
