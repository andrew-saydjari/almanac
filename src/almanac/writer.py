"""
A dedicated writer thread with a bounded queue.

Decouples HDF5 writes (and any other serial per-result work) from the loop
that collects worker results, so the parent process can keep dispatching and
receiving tasks while results are persisted in the background.

h5py is not thread-safe, so all writes must come from a single thread; this
module guarantees that by funnelling every write through one worker thread.
The caller must not touch the same HDF5 file while the writer is running
(start the writer after any read-phase, and ``close()`` it before any
post-run writes such as the missing-exposures table).
"""

import queue
import threading

__all__ = ["QueueWriter"]


class QueueWriter:
    """
    Run a write callable on items submitted from other threads.

    Items are processed strictly in submission order by a single background
    thread. The queue is bounded, providing backpressure: ``submit`` blocks
    when the writer falls behind, so memory use stays bounded.

    Failures are isolated per item: an exception raised by ``write`` is
    recorded in ``errors`` (and passed to ``on_error`` if given) and the
    writer moves on to the next item.

    :param write:
        Callable invoked as ``write(*args)`` for each submitted item.

    :param maxsize:
        Maximum number of queued (unwritten) items before ``submit`` blocks.

    :param on_error:
        Optional callable invoked as ``on_error(args, exception)`` when a
        write fails. Exceptions raised by the callback itself are ignored.
    """

    _SENTINEL = object()

    def __init__(self, write, maxsize: int = 8, on_error=None):
        self.write = write
        self.on_error = on_error
        self.queue = queue.Queue(maxsize=maxsize)
        self.errors = []  # [(args, exception), ...]
        self._abort = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="almanac-writer", daemon=True
        )

    # -- context manager -----------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Drain outstanding writes on a clean exit; abort (drop queued items
        # after the in-flight write finishes) if we are unwinding an exception
        # such as KeyboardInterrupt.
        self.close(drain=exc_type is None)
        return False

    # -- public API ----------------------------------------------------------
    def start(self):
        self._thread.start()
        return self

    def submit(self, *args):
        """
        Enqueue one item for writing. Blocks while the queue is full.

        Raises ``RuntimeError`` if the writer has been closed or aborted.
        """
        while True:
            if self._closed or self._abort.is_set():
                raise RuntimeError("cannot submit to a closed writer")
            try:
                self.queue.put(args, timeout=0.1)
            except queue.Full:
                continue
            else:
                return

    def close(self, drain: bool = True, timeout: float = None):
        """
        Stop the writer thread.

        :param drain:
            If true (default), process everything already queued before
            stopping. If false, discard queued items and stop as soon as the
            in-flight write (if any) finishes; nothing is interrupted
            mid-write, so the output file is never left truncated.

        :param timeout:
            Optional join timeout in seconds (default: wait indefinitely).
        """
        if self._closed:
            return
        self._closed = True
        if not self._thread.is_alive():
            return
        if not drain:
            self._abort.set()
            # Discard queued items so the sentinel is seen promptly.
            try:
                while True:
                    self.queue.get_nowait()
            except queue.Empty:
                pass
        # `put` (not `put_nowait`): the queue is bounded and may be full.
        # In the abort case we just cleared it, and the writer thread only
        # consumes, so this cannot deadlock.
        self.queue.put(self._SENTINEL)
        self._thread.join(timeout)

    # -- worker --------------------------------------------------------------
    def _run(self):
        while True:
            item = self.queue.get()
            if item is self._SENTINEL or self._abort.is_set():
                return
            try:
                self.write(*item)
            except Exception as exception:
                # Per-item fault isolation: record and continue, so one bad
                # night cannot poison the queue.
                self.errors.append((item, exception))
                if self.on_error is not None:
                    try:
                        self.on_error(item, exception)
                    except Exception:
                        pass
