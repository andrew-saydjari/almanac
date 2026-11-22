"""
Parallelism settings shared by every almanac command.

There are two knobs, with the same meaning in every command that has them:

``--processes`` / ``-p``
    Worker processes for file reading and computation (the main ``almanac``
    command and ``almanac postprocess``). Default: the CPU budget of this job
    (see `cpu_budget`).

``--query-workers``
    The most database connections almanac's worker processes hold at once
    (the main ``almanac`` command and ``almanac add metadata``). Default: the
    ``catalog_query_max_workers`` configuration setting, kept small so that
    the shared SDSS database (often reached through a single SSH tunnel) is
    not hit hard.

The CPU budget comes from, in order:

1. ``SLURM_CPUS_ON_NODE``: the number of CPUs this Slurm job was given on
   this node. This is the variable the ApogeeReduction.jl pipeline scripts
   use (``scripts/daily/run_all.sh``, ``scripts/bulk/run_bulk.sh``). If the
   process is pinned to fewer CPUs than that (``sched_getaffinity``, e.g.
   inside an ``srun`` step with fewer CPUs), the smaller number is used.
2. ``len(os.sched_getaffinity(0))``: the CPUs this process may run on.
3. ``os.cpu_count()``.

The whole-node ``os.cpu_count()`` is never used when a Slurm allocation or
the CPU affinity says otherwise.

In the main command each worker process queries the database for every
night (expected exposures from opsdb; with ``--fibers`` and without
``--no-x-match``, the sdss_id cross-match). When ``--processes`` is larger
than ``--query-workers``, a cross-process gate (`install_db_gate`) lets at
most ``--query-workers`` workers hold a database connection at a time: a
worker takes a slot at its first query of a night and closes its connection
and frees the slot when the night is done (`release_db_slot`). File reading
and parsing in the other workers carry on meanwhile.
"""

import os
from typing import Mapping, Optional, Tuple

#: Environment variable holding the Slurm job's CPU count on this node.
SLURM_CPU_VARIABLE = "SLURM_CPUS_ON_NODE"

#: Default for ``--query-workers`` if the configuration does not set one.
DEFAULT_QUERY_WORKERS = 4


def _positive_int(value) -> Optional[int]:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _affinity_count() -> Optional[int]:
    try:
        return len(os.sched_getaffinity(0)) or None
    except (AttributeError, OSError):  # not available on macOS
        return None


def cpu_budget(environ: Optional[Mapping[str, str]] = None, affinity: Optional[int] = -1,
               cpu_count: Optional[int] = -1) -> Tuple[int, str]:
    """
    Return ``(n_cpus, source)``: how many CPUs this process may use, and where
    that number came from (for logging).

    :param environ: environment to read (default: ``os.environ``).
    :param affinity: CPU-affinity count to use instead of
        ``len(os.sched_getaffinity(0))`` (``None`` means unavailable).
    :param cpu_count: value to use instead of ``os.cpu_count()``.
    """
    environ = os.environ if environ is None else environ
    if affinity == -1:
        affinity = _affinity_count()
    if cpu_count == -1:
        cpu_count = os.cpu_count()

    slurm = _positive_int(environ.get(SLURM_CPU_VARIABLE))
    if slurm is not None:
        if affinity is not None and affinity < slurm:
            return affinity, (
                f"CPU affinity ({affinity}; fewer than {SLURM_CPU_VARIABLE}={slurm})"
            )
        return slurm, f"Slurm {SLURM_CPU_VARIABLE}={slurm}"
    if affinity is not None:
        return affinity, f"CPU affinity (sched_getaffinity: {affinity})"
    return max(1, cpu_count or 1), f"os.cpu_count() ({cpu_count})"


def resolve_processes(explicit: Optional[int] = None, n_tasks: Optional[int] = None,
                      **budget_kwds) -> Tuple[int, str]:
    """
    Resolve the number of worker processes.

    An explicit positive value always wins. ``None`` (not given) or a negative
    value means the CPU budget (`cpu_budget`). ``0`` means 1 (run in this
    process). The result is at most ``n_tasks``, when given (there is no point
    in starting more workers than there are tasks).

    :returns: ``(n_processes, source)``.
    """
    if explicit is not None and explicit > 0:
        n, source = int(explicit), "--processes"
    elif explicit == 0:
        n, source = 1, "--processes 0 (serial)"
    else:
        n, source = cpu_budget(**budget_kwds)
        if explicit is not None:
            source = f"--processes {explicit} -> {source}"
    if n_tasks is not None and 0 <= n_tasks < n:
        n = max(1, n_tasks)
        source = f"{source}; capped at {n_tasks} task(s)"
    return n, source


def resolve_query_workers(explicit: Optional[int] = None, config=None,
                          allow_zero: bool = False) -> Tuple[int, str]:
    """
    Resolve the number of database query workers.

    An explicit value always wins (``0`` is kept only if ``allow_zero``: it
    means "query in this process" for ``almanac add metadata``; otherwise it
    is raised to 1). ``None`` means the ``catalog_query_max_workers``
    configuration setting (default 4).

    :returns: ``(n_query_workers, source)``.
    """
    if explicit is not None:
        n = int(explicit)
        floor = 0 if allow_zero else 1
        if n < floor:
            return floor, f"--query-workers {explicit} (raised to {floor})"
        return n, "--query-workers"
    if config is None:
        from almanac import config
    value = getattr(config, "catalog_query_max_workers", None)
    n = _positive_int(value)
    if n is None:
        return DEFAULT_QUERY_WORKERS, f"built-in default ({DEFAULT_QUERY_WORKERS})"
    return n, f"config catalog_query_max_workers={n}"


# --------------------------------------------------------------------------- #
# Cross-process cap on database connections (main command workers)
# --------------------------------------------------------------------------- #

_DB_GATE = None  # a multiprocessing semaphore, installed in pool workers
_DB_SLOT_HELD = False


def install_db_gate(gate) -> None:
    """Install (in a worker process) the semaphore that caps how many
    workers hold a database connection at once. ``None`` removes it."""
    global _DB_GATE, _DB_SLOT_HELD
    _DB_GATE = gate
    _DB_SLOT_HELD = False


def db_gate_installed() -> bool:
    return _DB_GATE is not None


def acquire_db_slot() -> None:
    """Take a database slot (blocking) unless this process holds one or no
    gate is installed. Called before every database query (see
    `almanac.retry.retry_on_database_error`)."""
    global _DB_SLOT_HELD
    if _DB_GATE is None or _DB_SLOT_HELD:
        return
    _DB_GATE.acquire()
    _DB_SLOT_HELD = True


def release_db_slot() -> None:
    """Close this process's database connection and free its slot (if it
    holds one). Called when a worker finishes a task."""
    global _DB_SLOT_HELD
    if _DB_GATE is None or not _DB_SLOT_HELD:
        return
    try:
        import sys

        module = sys.modules.get("almanac.database")
        if module is not None:
            module.database.close()
    except Exception:
        pass
    finally:
        _DB_SLOT_HELD = False
        _DB_GATE.release()


def run_task_releasing_db_slot(fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` in a pool worker, then free the worker's
    database slot (closing its connection) so another worker can use it."""
    try:
        return fn(*args, **kwargs)
    finally:
        release_db_slot()


def describe(processes: Tuple[int, str], query_workers: Optional[Tuple[int, str]] = None) -> str:
    """One log line with the resolved numbers and their sources."""
    text = f"processes={processes[0]} (from {processes[1]})"
    if query_workers is not None:
        text += f", query workers={query_workers[0]} (from {query_workers[1]})"
    return text
