"""Tests for the dedicated writer thread (almanac.writer.QueueWriter)."""

import threading
import time

import pytest

from almanac.writer import QueueWriter


class TestQueueWriter:
    def test_writes_all_items_in_order(self):
        written = []
        with QueueWriter(lambda *args: written.append(args)) as writer:
            for i in range(20):
                writer.submit("apo", i)
        assert written == [("apo", i) for i in range(20)]

    def test_completion_order_independence(self):
        # Items submitted in arbitrary (completion) order are all written,
        # exactly once, regardless of order.
        written = []
        items = [("apo", 60003), ("lco", 60001), ("apo", 60001), ("lco", 60009)]
        with QueueWriter(lambda *args: written.append(args)) as writer:
            for item in items:
                writer.submit(*item)
        assert written == items

    def test_failure_injection_does_not_poison_queue(self):
        written = []
        failures = []

        def write(observatory, mjd):
            if mjd == 2:
                raise ValueError("boom")
            written.append((observatory, mjd))

        writer = QueueWriter(write, on_error=lambda item, e: failures.append(item))
        with writer:
            for mjd in range(5):
                writer.submit("apo", mjd)

        assert written == [("apo", mjd) for mjd in (0, 1, 3, 4)]
        assert failures == [("apo", 2)]
        assert len(writer.errors) == 1
        (item, exception), = writer.errors
        assert item == ("apo", 2)
        assert isinstance(exception, ValueError)

    def test_on_error_callback_exception_is_ignored(self):
        written = []

        def write(mjd):
            if mjd == 0:
                raise ValueError("boom")
            written.append(mjd)

        def bad_callback(item, exception):
            raise RuntimeError("callback failed")

        with QueueWriter(write, on_error=bad_callback) as writer:
            writer.submit(0)
            writer.submit(1)
        assert written == [1]

    def test_bounded_queue_applies_backpressure(self):
        release = threading.Event()
        written = []

        def slow_write(i):
            release.wait(5)
            written.append(i)

        writer = QueueWriter(slow_write, maxsize=2).start()
        for i in range(3):  # 1 in-flight + 2 queued
            writer.submit(i)

        blocked = threading.Event()

        def try_submit():
            writer.submit(3)
            blocked.set()

        t = threading.Thread(target=try_submit, daemon=True)
        t.start()
        # The queue is full, so the extra submit must block...
        assert not blocked.wait(0.5)
        # ...until the writer makes progress.
        release.set()
        assert blocked.wait(5)
        t.join(5)
        writer.close()
        assert written == [0, 1, 2, 3]

    def test_close_drains_queue(self):
        written = []

        def slow_write(i):
            time.sleep(0.05)
            written.append(i)

        writer = QueueWriter(slow_write, maxsize=16).start()
        for i in range(10):
            writer.submit(i)
        writer.close(drain=True)
        assert written == list(range(10))
        assert not writer._thread.is_alive()

    def test_close_without_drain_discards_queued_items(self):
        started = threading.Event()
        release = threading.Event()
        written = []

        def slow_write(i):
            started.set()
            release.wait(5)
            written.append(i)

        writer = QueueWriter(slow_write, maxsize=16).start()
        for i in range(10):
            writer.submit(i)
        assert started.wait(5)  # first write is in flight
        release.set()
        writer.close(drain=False)
        # The in-flight write completes (no truncation) but queued items
        # are dropped; at most a couple more slip through the race between
        # clearing the queue and the sentinel.
        assert written[0] == 0
        assert len(written) < 10
        assert not writer._thread.is_alive()

    def test_abort_via_context_manager_on_exception(self):
        written = []

        with pytest.raises(KeyboardInterrupt):
            with QueueWriter(lambda i: written.append(i)) as writer:
                writer.submit(0)
                raise KeyboardInterrupt

        assert not writer._thread.is_alive()

    def test_submit_after_close_raises(self):
        writer = QueueWriter(lambda *args: None).start()
        writer.close()
        with pytest.raises(RuntimeError):
            writer.submit(1)

    def test_close_is_idempotent(self):
        writer = QueueWriter(lambda *args: None).start()
        writer.close()
        writer.close()
        writer.close(drain=False)
