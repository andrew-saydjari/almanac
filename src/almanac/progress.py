"""
Live progress bars for the almanac commands (`TaskDisplay`).

A `TaskDisplay` is a list of tasks drawn with `rich` as one live block:
status icon, description, bar and percentage, ``done/total unit``, rate,
elapsed time and ETA. It is drawn only on a terminal (see `progress_enabled`):

* both standard output and standard error must be terminals (a run started
  as ``almanac ... >> log 2>&1`` or under ``nohup`` draws nothing, so a log
  file never holds bar redraws, escape sequences or carriage returns);
* ``--no-progress`` on the command, or the environment variable
  ``ALMANAC_NO_PROGRESS`` set to anything but ``""``/``"0"``, turns it off;
* ``TERM=dumb`` turns it off.

When it is off, a `TaskDisplay` only keeps counters: the command writes
exactly what it writes without it.

While the block is drawn, log records (the ``logging`` stream handlers that
write to the terminal) and ``print`` output are routed through the same
`rich` console, so they appear above the block instead of being interleaved
with its redraws. The block is redrawn by `rich`'s refresh thread a few
times a second; updating a task only changes counters (no drawing in the
caller's loop).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
import threading
from threading import RLock
from typing import Dict, List, Optional, Tuple

__all__ = ["TaskDisplay", "TaskStatus", "Task", "progress_enabled", "NO_PROGRESS_ENV"]

#: Environment variable that turns the progress bars off (any value but "" or "0").
NO_PROGRESS_ENV = "ALMANAC_NO_PROGRESS"


def progress_enabled(no_progress: bool = False, streams=None) -> bool:
    """
    Whether live progress bars may be drawn: not ``no_progress``, not turned
    off by ``ALMANAC_NO_PROGRESS``, ``TERM`` is not ``dumb``, and every one of
    ``streams`` (default: standard output and standard error) is a terminal.
    """
    if no_progress:
        return False
    if os.environ.get(NO_PROGRESS_ENV, "").strip() not in ("", "0"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        try:
            if stream is None or not stream.isatty():
                return False
        except Exception:
            return False
    return True


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    name: str
    total: Optional[int] = 100
    completed: int = 0
    status: TaskStatus = TaskStatus.PENDING
    #: Unit of ``completed``/``total`` ("nights", "ids", ...); "" for a plain count.
    unit: str = ""
    #: Done before this run (a resumed checkpoint, skipped nights): shown in
    #: ``completed`` but not counted in the rate.
    initial: int = 0
    #: Short status shown after the description (e.g. a retry in progress).
    note: str = ""
    started: Optional[float] = None
    finished: Optional[float] = None
    #: Format ``completed``/``total`` as bytes.
    as_bytes: bool = False


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None or not seconds == seconds or seconds < 0 or seconds > 1e8:
        return "-:--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _format_count(n: float, as_bytes: bool = False) -> str:
    if as_bytes:
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(n) < 1024 or unit == "TiB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
    return f"{int(n):,}"


class TaskDisplay:
    """
    Live progress bars (see the module docstring).

    :param enabled: draw the bars (default: `progress_enabled()`). When False,
        the display only keeps counters and entering it does nothing.
    :param live: when False, never start a live block of its own; the caller
        draws `renderable()` inside its own `rich.live.Live` (the full-screen
        display of ``almanac -v``).
    :param refresh_per_second: redraw rate of the live block.
    """

    def __init__(self, enabled: Optional[bool] = None, live: bool = True,
                 refresh_per_second: float = 4):
        self.enabled = progress_enabled() if enabled is None else bool(enabled)
        self.tasks: Dict[int, Task] = {}
        self._lock = RLock()
        self._live = None
        self._own_live = bool(live)
        self._refresh_per_second = refresh_per_second
        self._routed: List[Tuple[logging.StreamHandler, object]] = []
        self.task_counter = 0
        self._console = None
        self._transient = False
        self._depth = 0
        self._pipe_r = self._pipe_w = None
        self._pump = None

    @property
    def console(self):
        if self._console is None:
            from rich.console import Console

            self._console = Console()
        return self._console

    # ------------------------------------------------------------------ #
    # Tasks (cheap: counters only, drawing happens in rich's refresh thread)
    # ------------------------------------------------------------------ #

    def add_task(self, name: str, total: Optional[int] = 100, unit: str = "",
                 initial: int = 0, as_bytes: bool = False) -> int:
        with self._lock:
            task_id = self.task_counter
            self.task_counter += 1
            self.tasks[task_id] = Task(
                name=name, total=total, unit=unit, initial=int(initial),
                completed=int(initial), as_bytes=as_bytes,
            )
            return task_id

    def _start(self, task: Task) -> None:
        if task.started is None:
            task.started = time.monotonic()
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING

    def start_task(self, task_id: int):
        with self._lock:
            self._start(self.tasks[task_id])

    def update_task(self, task_id: int, total: Optional[int] = None, name: Optional[str] = None,
                    note: Optional[str] = None):
        """Update task properties like total, name or note."""
        with self._lock:
            task = self.tasks[task_id]
            if total is not None:
                task.total = total
                if task.status == TaskStatus.COMPLETED and task.completed < total:
                    # More work arrived (e.g. another night queued for writing).
                    task.status, task.finished = TaskStatus.RUNNING, None
            if name is not None:
                task.name = name
            if note is not None:
                task.note = note
            self._start(task)

    def set_initial(self, task_id: int, initial: int):
        """Set the amount done before this run (never moves the bar back)."""
        with self._lock:
            task = self.tasks[task_id]
            task.completed += int(initial) - task.initial
            task.initial = int(initial)

    def set_completed(self, task_id: int, completed: int):
        """Set the amount done; the bar never goes backwards."""
        with self._lock:
            task = self.tasks[task_id]
            self.advance(task_id, max(0, int(completed) - task.completed))

    def advance(self, task_id: int, amount: int = 1):
        with self._lock:
            task = self.tasks[task_id]
            # Auto-start on first advance
            self._start(task)
            task.completed += amount
            # Auto-complete when we reach total
            if task.total is not None and task.completed >= task.total:
                self._finish(task, TaskStatus.COMPLETED)

    def _finish(self, task: Task, status: TaskStatus) -> None:
        if task.started is None:
            task.started = time.monotonic()
        if task.finished is None:
            task.finished = time.monotonic()
        task.status = status

    def complete(self, task_id: int):
        with self._lock:
            task = self.tasks[task_id]
            if task.total is not None:
                task.completed = max(task.completed, task.total)
            task.note = ""
            self._finish(task, TaskStatus.COMPLETED)

    def fail(self, task_id: int):
        with self._lock:
            self._finish(self.tasks[task_id], TaskStatus.FAILED)

    def _refresh(self):
        """Kept for compatibility: the live block redraws itself."""

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #

    @staticmethod
    def rate_and_eta(task: Task, now: Optional[float] = None):
        """(rate in units per second this run, elapsed s, ETA s) of a task."""
        now = time.monotonic() if now is None else now
        if task.started is None:
            return None, 0.0, None
        elapsed = (task.finished or now) - task.started
        done = task.completed - task.initial
        rate = done / elapsed if elapsed > 0 and done > 0 else None
        eta = None
        if task.status == TaskStatus.COMPLETED:
            eta = 0.0
        elif rate and task.total is not None:
            eta = max(task.total - task.completed, 0) / rate
        return rate, elapsed, eta

    def renderable(self):
        """The table of tasks (a `rich` renderable)."""
        return self._render()

    def _render(self):
        from rich.table import Table

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Status", width=2)
        table.add_column("Task", style="white", max_width=48, overflow="ellipsis", no_wrap=True)
        table.add_column("Progress", width=22, no_wrap=True)
        table.add_column("Count", justify="right", no_wrap=True)
        table.add_column("Rate", justify="right", no_wrap=True)
        table.add_column("Time", no_wrap=True)

        icons = {
            TaskStatus.PENDING: "[dim]○[/dim]",
            TaskStatus.RUNNING: "[yellow]●[/yellow]",
            TaskStatus.COMPLETED: "[green]✓[/green]",
            TaskStatus.FAILED: "[red]✗[/red]",
        }

        now = time.monotonic()
        with self._lock:
            tasks = [Task(**vars(t)) for t in self.tasks.values()]
        for task in tasks:
            pct = min(task.completed / task.total, 1.0) if task.total and task.total > 0 else 0
            if task.status == TaskStatus.COMPLETED:
                pct = 1.0 if not task.total else pct
            bar_width = 15
            filled = int(bar_width * pct)

            if task.status == TaskStatus.PENDING:
                bar = f"[dim]{'─' * bar_width}[/dim]"
            elif task.status == TaskStatus.COMPLETED:
                bar = f"[green]{'━' * bar_width}[/green]"
            elif task.status == TaskStatus.FAILED:
                bar = f"[red]{'━' * filled}[/red][dim]{'─' * (bar_width - filled)}[/dim]"
            else:
                bar = f"[yellow]{'━' * filled}[/yellow][dim]{'─' * (bar_width - filled)}[/dim]"

            progress = f"{bar} [dim]{pct:>5.1%}[/dim]"
            name_style = "dim" if task.status == TaskStatus.PENDING else "white"
            name = f"[{name_style}]{_escape(task.name)}[/{name_style}]"
            if task.note:
                name += f" [dim]({_escape(task.note)})[/dim]"

            unit = f" {task.unit}" if task.unit else ""
            total = "?" if task.total is None else _format_count(task.total, task.as_bytes)
            count = f"{_format_count(task.completed, task.as_bytes)}/{total}" + ("" if task.as_bytes else unit)

            rate, elapsed, eta = self.rate_and_eta(task, now)
            if rate is None or (task.total == 1 and not task.unit):
                rate_text = ""  # a single step has no meaningful rate
            elif task.as_bytes:
                rate_text = f"{_format_count(rate, True)}/s"
            else:
                rate_text = f"{rate:,.1f}{unit}/s" if rate < 100 else f"{rate:,.0f}{unit}/s"
            if task.started is None:
                time_text = ""
            elif task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                time_text = f"{_format_duration(elapsed)}"
            else:
                time_text = f"{_format_duration(elapsed)} ETA {_format_duration(eta)}"

            table.add_row(icons[task.status], name, progress, f"[dim]{count}[/dim]",
                          f"[dim]{rate_text}[/dim]", f"[dim]{time_text}[/dim]")

        return table

    # ------------------------------------------------------------------ #
    # Live block
    # ------------------------------------------------------------------ #

    def _route_logging(self) -> None:
        """Point stream handlers that write to the terminal at rich's proxies."""
        proxies = {}
        for proxy in (sys.stdout, sys.stderr):
            original = getattr(proxy, "rich_proxied_file", None)
            if original is not None:
                proxies[id(original)] = proxy
        if not proxies:
            return
        loggers = [logging.getLogger()] + [
            lg for lg in list(logging.Logger.manager.loggerDict.values())
            if isinstance(lg, logging.Logger)
        ]
        for lg in loggers:
            for handler in list(lg.handlers):
                if type(handler) is logging.FileHandler or not isinstance(handler, logging.StreamHandler):
                    continue
                proxy = proxies.get(id(getattr(handler, "stream", None)))
                if proxy is not None:
                    self._routed.append((handler, handler.stream))
                    handler.stream = proxy

    def _unroute_logging(self) -> None:
        for handler, stream in reversed(self._routed):
            try:
                if handler.stream is not stream:
                    handler.stream.flush()
            except Exception:
                pass
            handler.stream = stream
        self._routed = []

    def session(self, transient: bool = False):
        """
        Context manager that draws the live block while it is open (if
        enabled). ``transient``: erase the block when it closes (for a step
        shown before the main block of a command). ``with display:`` is
        ``with display.session():``. Re-entering an open display does nothing.
        """
        self._transient = bool(transient)
        return self

    def __enter__(self):
        transient, self._transient = self._transient, False
        if self.enabled and self._own_live and self._live is None:
            from rich.live import Live

            self._live = Live(
                console=self.console,
                get_renderable=self._render,
                refresh_per_second=self._refresh_per_second,
                redirect_stdout=True,
                redirect_stderr=True,
                transient=transient,
            )
            self._live.__enter__()
            self._route_logging()
            self._start_pump()
            self._depth = 1
        elif self._live is not None:
            self._depth += 1
        return self

    def __exit__(self, *args):
        if self._live is not None and self._depth > 1:
            self._depth -= 1
            return False
        if self._live is not None:
            try:
                self._stop_pump()
                self._unroute_logging()
            finally:
                live, self._live = self._live, None
                global _ACTIVE
                if _ACTIVE is self:
                    _ACTIVE = None
                live.__exit__(*args)
        return False

    # Worker processes forked while the block is drawn: their standard output
    # and error (log records included) go through a pipe that a thread of this
    # process prints above the block, so they neither break the block nor
    # touch rich's console (whose locks a forked child cannot rely on).

    def _start_pump(self) -> None:
        global _ACTIVE
        try:
            self._pipe_r, self._pipe_w = os.pipe()
        except OSError:
            self._pipe_r = self._pipe_w = None
            return
        self._pump = threading.Thread(
            target=self._pump_loop, args=(self._pipe_r,), name="almanac-progress-pump", daemon=True
        )
        self._pump.start()
        _ACTIVE = self

    def _stop_pump(self, timeout: float = 2.0) -> None:
        w, self._pipe_w = self._pipe_w, None
        if w is None:
            return
        try:
            os.close(w)  # the reader sees end-of-file once every child has exited
        except OSError:
            pass
        if self._pump is not None:
            self._pump.join(timeout)
        self._pump = None

    def _pump_loop(self, fd: int) -> None:
        pending = b""
        try:
            while True:
                try:
                    data = os.read(fd, 65536)
                except InterruptedError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                *lines, pending = (pending + data).split(b"\n")
                if lines:
                    self._emit([line.decode(errors="replace") for line in lines])
            if pending:
                self._emit([pending.decode(errors="replace")])
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _emit(self, lines: List[str]) -> None:
        live = self._live
        if live is not None:
            try:
                from rich.ansi import AnsiDecoder
                from rich.text import Text

                decoder = AnsiDecoder()
                self.console.print(Text("\n").join(decoder.decode_line(line) for line in lines))
                return
            except Exception:
                pass
        stream = sys.__stderr__
        try:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
        except Exception:
            pass

    def _detach_in_child(self) -> None:
        """In a forked child: plain streams, standard output/error into the pipe."""
        for name in ("stdout", "stderr"):
            original = getattr(getattr(sys, name), "rich_proxied_file", None)
            if original is not None:
                setattr(sys, name, original)
        for handler, stream in self._routed:
            handler.stream = stream
        self._routed = []
        self._live = None
        self._pump = None
        r, w = self._pipe_r, self._pipe_w
        self._pipe_r = self._pipe_w = None
        if w is not None:
            try:
                os.dup2(w, 1)
                os.dup2(w, 2)
                os.close(w)
            except OSError:
                pass
        if r is not None:
            try:
                os.close(r)
            except OSError:
                pass


#: The `TaskDisplay` drawing a live block in this process (None if none).
_ACTIVE: Optional["TaskDisplay"] = None


def _after_fork_in_child() -> None:
    global _ACTIVE
    display, _ACTIVE = _ACTIVE, None
    if display is not None:
        display._detach_in_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _escape(text: str) -> str:
    from rich.markup import escape

    return escape(str(text))
