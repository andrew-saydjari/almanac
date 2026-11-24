#!/usr/bin/env python3

import click


def _pool_initializer(apogee_id_lookup=None, apogee_id_source=None, db_gate=None):
    """
    Initialize a process-pool worker.

    Resets any database connection state inherited from the parent (each
    worker must open its own connection), and installs the parent-built
    full APOGEE ID -> SDSS ID lookup, if one was provided
    (`--full-apogee-id-lookup`), so that workers do not each re-run the heavy
    query that builds it. Without one, workers query only the APOGEE IDs
    their nights need.

    With `db_gate` (a multiprocessing semaphore; given when --processes is
    larger than --query-workers), at most that many workers hold a database
    connection at once (see `almanac.parallel`).
    """
    from almanac import parallel
    from almanac.signals import worker_signals

    # Stopping is the parent's job (it kills the workers on a signal); a
    # worker dies with its parent (Linux) rather than run on as an orphan.
    worker_signals()
    parallel.install_db_gate(db_gate)

    from sdssdb.peewee.sdss5db import database

    if hasattr(database, "_state"):
        database._state.closed = True
        database._state.conn = None
    if db_gate is not None:
        # Importing almanac.database connects (if the module is not already
        # inherited from the parent): do it inside a slot, then close.
        with db_gate:
            from almanac.database import database
            try:
                database.close()
            except Exception:
                pass
    else:
        from almanac.database import database

    from almanac import apogee

    if apogee_id_lookup is not None:
        apogee.set_apogee_id_lookup(apogee_id_lookup)
    if apogee_id_source is not None:
        # The parent already resolved (and logged) the lookup source.
        apogee.set_apogee_id_source(apogee_id_source)


# Night categories that count as failed (the night is not in the file). The
# exit status of each category is defined in `almanac.exit_codes`.
FAILED_CATEGORIES = ("exception", "database_error", "write_failed", "pool_broken")


def _failure_category(exception) -> str:
    """
    Classify the exception that made a night fail: ``database_error`` for a
    database connection error that persisted after the retries (peewee
    OperationalError / InterfaceError) or a cross-match without a database
    connection (`apogee.DatabaseUnavailableError`), else ``exception`` (a
    bad plugmap or confSummary, an unreadable raw file, a bug, ...).
    """
    from peewee import InterfaceError, OperationalError
    from almanac.apogee import DatabaseUnavailableError

    if isinstance(exception, (OperationalError, InterfaceError, DatabaseUnavailableError)):
        return "database_error"
    return "exception"


def _run_categories(failed, db_unavailable_keys, cross_match_requested):
    """The problem categories of a run (keys of `exit_codes.CATEGORY_EXIT_STATUS`)."""
    categories = {category for _, _, category, _ in failed}
    if db_unavailable_keys:
        categories.add("db_unavailable_cross_match" if cross_match_requested else "db_unavailable")
    return categories


def _run_exit_status(failed, db_unavailable_keys, cross_match_requested) -> int:
    """
    Exit status of the main command: the highest `almanac.exit_codes.ExitStatus`
    of the problems of the run (`failed`: [(observatory, mjd, category,
    message), ...]; `db_unavailable_keys`: nights written without the
    operations database), 0 if none. Nights without data and nights skipped by
    ``--skip-existing`` are not problems.
    """
    from almanac.exit_codes import exit_status_for

    return int(exit_status_for(_run_categories(failed, db_unavailable_keys, cross_match_requested)))


def _exit_status_name(status: int) -> str:
    """`ExitStatus` name of an exit status, or SIG<NAME> for 128 + signal."""
    import signal
    from almanac.exit_codes import ExitStatus

    try:
        return ExitStatus(status).name
    except ValueError:
        try:
            return signal.Signals(status - 128).name
        except ValueError:
            return str(status)


def _run_summary(counts, failed, db_unavailable_keys, exit_status):
    """Log the end-of-run summary (always): counts per category, failed nights."""
    from almanac import logger

    problems = bool(failed or db_unavailable_keys or counts.get("not_processed"))
    log = logger.warning if problems or exit_status else logger.info
    log(
        "Run summary: "
        + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items())
        + f"; exit status {exit_status} ({_exit_status_name(exit_status)})"
    )
    if failed:
        logger.warning(f"{len(failed)} observatory/MJD pairs failed and were skipped:")
        for observatory, mjd, category, message in failed:
            logger.warning(f"  - {observatory}/{mjd} [{category}]: {message}")
    if db_unavailable_keys:
        logger.warning(
            f"{len(db_unavailable_keys)} observatory/MJD pairs were built without the "
            "operations database (expected exposures not checked; missing_exposures "
            "reason db_unavailable): "
            + ", ".join(f"{o}/{m}" for o, m in sorted(db_unavailable_keys))
        )


@click.group(invoke_without_command=True)
@click.option("-v", "--verbosity", count=True,
              help="Verbosity: -v live progress display; -vv also the exposure table of every night")
@click.option(
    "--mjd",
    default=None,
    type=int,
    help="Modified Julian date to query. Use negative values to indicate relative to current MJD",
)
@click.option("--mjds", default=None, type=str,
              help="Comma-separated explicit MJD list (non-contiguous OK)")
@click.option("--mjd-start", default=None, type=int, help="Start of MJD range to query")
@click.option("--mjd-end", default=None, type=int, help="End of MJD range to query")
@click.option("--date", default=None, type=str, help="Date to query (e.g., 2024-01-15)")
@click.option(
    "--date-start", default=None, type=str, help="Start of date range to query"
)
@click.option("--date-end", default=None, type=str, help="End of date range to query")
@click.option("--apo", is_flag=True, help="Query Apache Point Observatory data")
@click.option("--lco", is_flag=True, help="Query Las Campanas Observatory data")
@click.option(
    "--fibers", "--fibres", is_flag=True,
    help="Include the fiber-to-target mappings (cross-matched to sdss_id unless --no-x-match)"
)
@click.option(
    "--no-x-match", is_flag=True, help="Do not cross-match targets with SDSS-V database"
)
@click.option(
    "--no-overrides",
    is_flag=True,
    help=(
        "Do not apply the exposure overrides in almanac/etc/exposure_overrides.yaml "
        "(for diffing their effect)"
    ),
)
@click.option(
    "--full-apogee-id-lookup",
    is_flag=True,
    help=(
        "Build the full APOGEE ID -> SDSS ID lookup (every DR17 allStar "
        "entry; one heavy query) once up front, instead of querying only the "
        "APOGEE IDs each night needs"
    ),
)
@click.option(
    "--live-apogee-id-join",
    is_flag=True,
    help=(
        "Never read the APOGEE ID -> SDSS ID lookup table "
        "(config `apogee_id_lookup_view`); always use the live database join"
    ),
)
@click.option("--output", "-O", default=None, type=str, help="Output file")
@click.option(
    "--processes", "-p", default=None, type=int,
    help=(
        "Worker processes (one observatory/MJD each). Default: the CPU budget "
        "(Slurm SLURM_CPUS_ON_NODE, else the CPU affinity, else all CPUs); "
        "negative values mean the same; 0 or 1 runs in this process. Never "
        "more than the number of observatory/MJD pairs."
    ),
)
@click.option(
    "--query-workers", default=None, type=int,
    help=(
        "Most worker processes that hold a database connection at once "
        "(default: the catalog_query_max_workers configuration setting). "
        "Workers beyond this keep reading files while they wait."
    ),
)
@click.option(
    "--skip-existing",
    is_flag=True,
    help="Skip observatory/MJD pairs that already exist in the output file (resume mode)",
)
@click.pass_context
def main(
    ctx,
    verbosity,
    mjd,
    mjds,
    mjd_start,
    mjd_end,
    date,
    date_start,
    date_end,
    apo,
    lco,
    fibers,
    no_x_match,
    no_overrides,
    full_apogee_id_lookup,
    live_apogee_id_join,
    output,
    processes,
    query_workers,
    skip_existing,
):
    """
    Almanac collects metadata from planned and actual APOGEE exposures,
    and identifies sequences of exposures that constitute epoch visits.
    """

    # This keeps the default behaviour as 'query mode' but allows for commands like 'config'.
    if ctx.invoked_subcommand is not None:
        return  # Let Click handle the subcommand

    import functools
    import h5py as h5
    from itertools import product
    from rich.live import Live
    from almanac.display import ObservationsDisplay, display_exposures
    from almanac import apogee, logger, io, utils
    from almanac.writer import QueueWriter
    from contextlib import nullcontext
    from time import time, sleep

    mjds, mjd_min, mjd_max = utils.parse_mjds(
        mjd, mjd_start, mjd_end, date, date_start, date_end, mjds=mjds
    )
    observatories = utils.get_observatories(apo, lco)

    tasks = list(product(mjds, observatories))
    n_requested = len(tasks)
    n_skipped = 0
    results = []
    failed = []  # [(observatory, mjd, category, error message), ...]
    no_data_keys = set()  # (observatory, mjd) processed, no exposure files
    missing_rows = []  # run-level missing-exposures report records
    processed_keys = set()  # (observatory, mjd) completed in this run

    display = ObservationsDisplay(mjd_min, mjd_max, observatories)

    buffered_critical_logs = []
    buffered_result_rows = []

    refresh_per_second = 1
    context_manager = (
        Live(
            display.create_display(), refresh_per_second=refresh_per_second, screen=True
        )
        if verbosity >= 1
        else nullcontext()
    )
    io_kwds = dict(fibers=fibers, compression=False)
    from almanac.overrides import load_overrides, overrides_attrs
    if not no_overrides:
        load_overrides()  # a malformed override file fails here, before any work
    override_attrs = overrides_attrs(not no_overrides)
    from almanac import parallel
    from almanac.signals import GracefulStop

    # SIGINT, SIGTERM (`kill PID`, a Slurm time limit) and SIGHUP (unless
    # ignored, as under nohup) stop the run cleanly: the workers are killed,
    # the nights already processed are written (at most max(4, 2 x
    # --processes) are queued), the
    # missing-exposures table and run summary are written, and the exit
    # status is 128 + the signal number. SIGINT is handled even when the
    # shell started the job with SIGINT ignored (background jobs).
    #
    # The output file is opened only while something is written to it: for
    # the --skip-existing check, once per night (flushed and closed), and at
    # the end. Workers are never forked while it is open (they do not hold
    # its descriptor or lock), and a run killed outright (SIGKILL) leaves a
    # file that is readable up to the last night written; nights written
    # whose missing-exposure rows were not recorded yet stay listed in
    # /missing_exposures_pending and are processed again by the next run.
    stop = GracefulStop()
    interrupted = None  # the signal that stopped the run, if any
    written_keys = set()  # (observatory, mjd) written to the output file
    pending_before = set()  # nights left pending by an earlier run
    writer = None

    with stop:
        try:
            if output:
                with io.open_for_writing(output) as fp:
                    pending_before = io.read_pending_missing(fp)
                    if skip_existing:
                        kept = []
                        for mjd, observatory in tasks:
                            if (
                                f"raw/{observatory}/{mjd}/exposures" in fp
                                and (observatory, mjd) not in pending_before
                            ):
                                display.completed[observatory].add(
                                    mjd - mjd_min + display.offset
                                )
                            else:
                                kept.append((mjd, observatory))
                        n_skipped = len(tasks) - len(kept)
                        if n_skipped:
                            logger.info(
                                f"Skipping {n_skipped} observatory/MJD pairs already "
                                f"present in {output}"
                            )
                        tasks = kept

            processes_resolved = parallel.resolve_processes(processes, n_tasks=len(tasks))
            query_workers_resolved = parallel.resolve_query_workers(query_workers)
            processes = processes_resolved[0]
            query_workers = query_workers_resolved[0]
            # Each worker queries the database for every night (opsdb expected
            # exposures; the cross-match with --fibers). Cap the connections they
            # hold at once when there are more workers than query workers.
            use_db_gate = processes > 1 and processes > query_workers
            logger.info(
                f"Parallelism: {parallel.describe(processes_resolved, query_workers_resolved)}; "
                + (
                    f"at most {query_workers} worker(s) hold a database connection at once"
                    if use_db_gate
                    else (
                        "running in this process"
                        if processes <= 1
                        else f"each of the {processes} workers holds its own database connection"
                    )
                )
            )

            # All HDF5 writes during the run go through a dedicated writer thread
            # with a bounded queue, so the loop below can keep collecting worker
            # results while the previous night is being written. h5py is not
            # thread-safe: this thread does not open the file again until the
            # writer is closed.
            if output:

                def _write_night(observatory_w, mjd_w, exposures_w, sequences_w):
                    with io.open_for_writing(output) as fp_w:
                        io.update(fp_w, observatory_w, mjd_w, exposures_w, sequences_w, **io_kwds)
                        # Overrides on/off, and which APOGEE ID -> SDSS ID source
                        # cross-matching used (shared with `add fibers`).
                        io.write_night_attrs(
                            fp_w, observatory_w, mjd_w, override_attrs, lookup_attrs
                        )
                        io.add_pending_missing(fp_w, observatory_w, mjd_w)
                    written_keys.add((observatory_w, mjd_w))

                def _on_write_error(item, exception):
                    observatory_w, mjd_w = item[:2]
                    logger.error(f"Failed to write {observatory_w}/{mjd_w}: {exception}")
                    failed.append((observatory_w, mjd_w, "write_failed", str(exception)))

                writer = QueueWriter(
                    _write_night,
                    maxsize=max(4, 2 * (processes or 1)),
                    on_error=_on_write_error,
                ).start()

            # Decide once, in this process, whether APOGEE ID lookups read the
            # materialized view or run the live join (and, opt-in, build the full
            # lookup); record the choice per night. Shared with `add fibers`.
            apogee_id_source, lookup_attrs, full_lookup = apogee.prepare_cross_match(
                bool(fibers and not no_x_match and tasks),
                live_apogee_id_join=live_apogee_id_join,
                full_apogee_id_lookup=full_apogee_id_lookup,
            )

            with context_manager as live:
                if processes > 1:

                    # Parallel
                    import os
                    import sys
                    import signal
                    import multiprocessing
                    import concurrent.futures
                    from concurrent.futures.process import BrokenProcessPool

                    mp_context = multiprocessing.get_context()
                    db_gate = (
                        mp_context.BoundedSemaphore(query_workers) if use_db_gate else None
                    )
                    if "almanac.database" in sys.modules:
                        # The parent is done with the database (lookup-source
                        # probe, optional full lookup): close its connection so
                        # it neither counts against the database nor is shared
                        # with forked workers.
                        try:
                            sys.modules["almanac.database"].database.close()
                        except Exception:
                            pass

                    # With --full-apogee-id-lookup, ship the parent-built full
                    # lookup to every worker through the pool initializer, so no
                    # worker re-runs the heavy query. Otherwise each worker
                    # queries (and memoises) only the APOGEE IDs its nights need.
                    initargs = (full_lookup, apogee_id_source, db_gate)
                    # With the gate, a worker frees its database slot (closing
                    # its connection) when it finishes a night.
                    task_fn = (
                        functools.partial(
                            parallel.run_task_releasing_db_slot, apogee.get_almanac_data
                        )
                        if use_db_gate
                        else apogee.get_almanac_data
                    )

                    with concurrent.futures.ProcessPoolExecutor(
                        max_workers=processes,
                        initializer=_pool_initializer,
                        initargs=initargs,
                        mp_context=mp_context,
                    ) as pool:

                        task_iter = iter(tasks)
                        futures = {}  # future -> (mjd, observatory)
                        try:
                            for n, (mjd, observatory) in enumerate(task_iter, start=1):
                                futures[
                                    pool.submit(
                                        task_fn,
                                        observatory,
                                        mjd,
                                        fibers,
                                        not no_x_match,
                                        overrides=not no_overrides,
                                    )
                                ] = (mjd, observatory)
                                if n == processes:
                                    break

                            t = time()
                            while len(futures) > 0:

                                future = next(concurrent.futures.as_completed(futures))
                                mjd, observatory = futures.pop(future)
                                v = mjd - mjd_min + display.offset

                                try:
                                    observatory, mjd, exposures, sequences, missing = (
                                        result
                                    ) = future.result()
                                except KeyboardInterrupt:
                                    raise
                                except BrokenProcessPool:
                                    failed.append(
                                        (observatory, mjd, "pool_broken", "process pool broken")
                                    )
                                    raise
                                except Exception as e:
                                    # Per-MJD fault isolation: record and continue.
                                    logger.exception(
                                        f"Failed to process {observatory}/{mjd}: {e}"
                                    )
                                    failed.append(
                                        (observatory, mjd, _failure_category(e), str(e))
                                    )
                                else:
                                    processed_keys.add((observatory, mjd))
                                    missing_rows.extend(missing)

                                    if any(
                                        e.image_type == "missing" for e in exposures
                                    ):
                                        display.missing.add(v)

                                    if not exposures:
                                        display.no_data[observatory].add(v)
                                        no_data_keys.add((observatory, mjd))
                                    else:
                                        display.completed[observatory].add(v)
                                        results.append(result)
                                        if writer is not None:
                                            writer.submit(
                                                observatory,
                                                mjd,
                                                exposures,
                                                sequences,
                                            )

                                if (
                                    live is not None
                                    and (time() - t) > 1 / refresh_per_second
                                ):
                                    live.update(display.create_display())
                                    t = time()

                                try:
                                    mjd, observatory = next(task_iter)
                                except StopIteration:
                                    None
                                else:
                                    futures[
                                        pool.submit(
                                            task_fn,
                                            observatory,
                                            mjd,
                                            fibers,
                                            not no_x_match,
                                            overrides=not no_overrides,
                                        )
                                    ] = (mjd, observatory)

                        except BrokenProcessPool as e:
                            # The pool cannot recover; mark everything outstanding
                            # as failed but continue so the missing-exposures table
                            # and failure report are still written.
                            logger.error(f"Process pool broke: {e}")
                            for mjd_o, obs_o in futures.values():
                                failed.append((obs_o, mjd_o, "pool_broken", "process pool broken"))
                            for mjd_o, obs_o in task_iter:
                                failed.append((obs_o, mjd_o, "pool_broken", "process pool broken"))
                        except KeyboardInterrupt:
                            with stop.deferred():
                                for pid in list(pool._processes):
                                    try:
                                        os.kill(pid, signal.SIGKILL)
                                    except OSError:
                                        pass
                                pool.shutdown(wait=False, cancel_futures=True)
                            raise
                else:
                    t = time()
                    for mjd, observatory in tasks:
                        v = mjd - mjd_min + display.offset
                        try:
                            (
                                observatory, mjd, exposures, sequences, missing
                            ) = result = apogee.get_almanac_data(
                                observatory, mjd, fibers, not no_x_match,
                                overrides=not no_overrides,
                            )
                        except KeyboardInterrupt:
                            raise
                        except Exception as e:
                            # Per-MJD fault isolation: record and continue.
                            logger.exception(
                                f"Failed to process {observatory}/{mjd}: {e}"
                            )
                            failed.append((observatory, mjd, _failure_category(e), str(e)))
                        else:
                            processed_keys.add((observatory, mjd))
                            missing_rows.extend(missing)

                            if any(e.image_type == "missing" for e in exposures):
                                display.missing.add(v)

                            if not exposures:
                                display.no_data[observatory].add(v)
                                no_data_keys.add((observatory, mjd))
                            else:
                                display.completed[observatory].add(v)
                                results.append(result)
                                if writer is not None:
                                    writer.submit(
                                        observatory,
                                        mjd,
                                        exposures,
                                        sequences,
                                    )

                        if live is not None and (time() - t) > 1 / refresh_per_second:
                            live.update(display.create_display())
                            t = time()

                if live is not None:
                    live.update(display.create_display())
                    if verbosity <= 1 and output is None:
                        sleep(3)

        except KeyboardInterrupt as e:
            # Includes almanac.signals.StopRequested (SIGINT/SIGTERM/SIGHUP).
            import signal as _signal
            interrupted = getattr(e, "signum", _signal.SIGINT)
            logger.warning(
                f"Stopped by signal {interrupted}: writing the nights already processed, "
                "the missing-exposures table and the run summary"
            )

        with stop.deferred():
            if writer is not None:
                # Write all queued nights (also when stopped by a signal: they
                # are already processed, and the queue is short) and stop the
                # writer thread before this thread opens the file again.
                writer.close(drain=True)

            # End-of-run accounting (see `_run_exit_status`).
            failed_keys = {(o, m) for o, m, _, _ in failed}
            # Nights whose missing-exposure rows are recorded now: those written
            # to the file and those without data (a night whose write failed
            # or was dropped by a stop keeps its previous rows).
            done_keys = (written_keys if output else processed_keys - no_data_keys) | no_data_keys
            done_missing_rows = [
                r for r in missing_rows if (r["observatory"], r["mjd"]) in done_keys
            ]
            db_unavailable_keys = {
                (r["observatory"], r["mjd"]) for r in done_missing_rows
                if r["reason"] == "db_unavailable"
            } - failed_keys
            cross_match_requested = bool(fibers and not no_x_match)
            exit_status = _run_exit_status(failed, db_unavailable_keys, cross_match_requested)
            if interrupted is not None:
                exit_status = 128 + int(interrupted)
            counts = dict(
                requested=n_requested,
                skipped_existing=n_skipped,
                with_data=len(done_keys - no_data_keys - failed_keys),
                no_data=len(no_data_keys),
                failed=len(failed_keys),
                **{
                    category: len({(o, m) for o, m, c, _ in failed if c == category})
                    for category in FAILED_CATEGORIES
                },
                db_unavailable=len(db_unavailable_keys),
                not_processed=max(
                    0, n_requested - n_skipped - len(done_keys | failed_keys)
                ),
                # Opens of the output retried because its lock was briefly
                # taken (see io.open_h5_retrying); not a problem by itself.
                lock_retries=io.lock_retry_count,
            )

            if output:
                from datetime import datetime, timezone
                from almanac import __version__

                with io.open_for_writing(output) as fp:
                    # Run-level missing-exposures table. Entries from previous
                    # runs for (observatory, mjd) pairs not done here (skipped,
                    # failed, not reached) are preserved.
                    io.write_missing_exposures(fp, done_missing_rows, replace_keys=done_keys)
                    io.set_pending_missing(
                        fp, (pending_before | written_keys) - done_keys
                    )
                    # Nights with a problem (accumulated over runs into this
                    # file) and the counts of this run, so a build can be
                    # audited later.
                    summary_rows = [
                        dict(observatory=o, mjd=m, category=c, message=msg)
                        for o, m, c, msg in failed
                    ] + [
                        dict(observatory=o, mjd=m, category="db_unavailable", message="")
                        for o, m in sorted(db_unavailable_keys)
                    ]
                    io.write_run_summary(
                        fp,
                        summary_rows,
                        attrs={
                            **{f"last_run_n_{k}": v for k, v in counts.items()},
                            "last_run_exit_status": exit_status,
                            "last_run_exit_status_name": _exit_status_name(exit_status),
                            "last_run_finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "last_run_almanac_version": __version__,
                            "last_run_cross_match": cross_match_requested,
                        },
                        replace_keys=done_keys | failed_keys,
                    )

    if verbosity >= 2:
        for observatory, mjd, exposures, sequences, _missing in results:
            display_exposures(exposures, sequences)

        # Show critical logs at the end to avoid disrupting the display
        for item in buffered_critical_logs:
            logger.critical(item)

    if missing_rows:
        n_mjds = len({(r["observatory"], r["mjd"]) for r in missing_rows})
        logger.warning(
            f"Recorded {len(missing_rows)} missing-exposure entries across "
            f"{n_mjds} observatory/MJD pairs"
            + (" (see /missing_exposures in the output file)" if output else "")
        )

    _run_summary(counts, failed, db_unavailable_keys, exit_status)
    if failed and output:
        logger.warning(
            "Re-run the same command with --skip-existing to retry only "
            "the failed pairs."
        )
    if exit_status:
        ctx.exit(exit_status)


def _lookup_night_writer(output):
    """
    For `almanac lookup -O`: a function `(observatory, mjd)` that builds the
    night as `almanac --fibers` does (overrides on, cross-matched) and writes
    it to `output` with the same writers, night attributes included
    (`io.write_night_attrs`), in its own write session.
    """
    from almanac import apogee, io
    from almanac.overrides import load_overrides, overrides_attrs

    load_overrides()
    override_attrs = overrides_attrs(True)
    _source, lookup_attrs, _full = apogee.prepare_cross_match(True)

    def write_night(observatory, mjd):
        """None if written, else the failure category (see almanac.exit_codes)."""
        from almanac import logger

        try:
            observatory, mjd, exposures, sequences, _missing = apogee.get_almanac_data(
                observatory, mjd, fibers=True, meta=True
            )
        except Exception as e:
            logger.exception(f"Failed to process {observatory}/{mjd}: {e}")
            return _failure_category(e)
        try:
            with io.open_for_writing(output) as fp:
                io.update(fp, observatory, mjd, exposures, sequences, fibers=True, compression=False)
                io.write_night_attrs(fp, observatory, mjd, override_attrs, lookup_attrs)
        except Exception as e:
            logger.exception(f"Failed to write {observatory}/{mjd}: {e}")
            return "write_failed"
        return None

    return write_night


@main.command()
@click.argument("identifiers", type=int, nargs=-1)
@click.option("--output", "-O", default=None, type=str,
              help="Also write the nights with matching exposures to this almanac file (as `almanac --fibers`)")
def lookup(identifiers, output, **kwargs):
    """Lookup target(s) by catalog or SDSS identifier."""

    if not identifiers:
        return

    import h5py as h5
    from contextlib import nullcontext
    from peewee import fn
    from itertools import chain, starmap
    from almanac import io
    from almanac.database import database
    from almanac.apogee import get_exposures, get_almanac_data
    from sdssdb.peewee.sdss5db.targetdb import (
        Assignment,
        AssignmentStatus,
        CartonToTarget,
        Target,
        Hole,
        Observatory,
        Design,
        DesignToField,
        Field,
    )
    from sdssdb.peewee.sdss5db.catalogdb import SDSS_ID_flat

    from rich.table import Table as RichTable
    from rich.console import Console
    from rich.live import Live

    sq = (
        SDSS_ID_flat.select(SDSS_ID_flat.sdss_id)
        .where(
            (SDSS_ID_flat.sdss_id.in_(identifiers))
            | (SDSS_ID_flat.catalogid.in_(identifiers))
        )
        .alias("sq")
    )
    q = (
        SDSS_ID_flat.select(SDSS_ID_flat.catalogid, SDSS_ID_flat.sdss_id)
        .distinct()
        .join(sq, on=(SDSS_ID_flat.sdss_id == sq.c.sdss_id))
        .order_by(SDSS_ID_flat.sdss_id.asc())
        .tuples()
    )

    catalogids = {catalogid: sdss_id for catalogid, sdss_id in q}

    if not catalogids:
        raise click.ClickException(
            f"Identifiers {identifiers} not found in SDSS-V database"
        )

    # The targetdb models are declared without their schema.
    for m in (
        Target,
        Observatory,
        AssignmentStatus,
        Field,
        CartonToTarget,
        Assignment,
        Hole,
        Design,
        DesignToField,
    ):
        m._meta.schema = "targetdb"

    q = (
        Target.select(
            fn.Lower(Observatory.label),
            AssignmentStatus.mjd,
            Field.field_id,
        )
        .distinct()
        .join(CartonToTarget)
        .join(Assignment)
        .join(AssignmentStatus)
        .switch(Assignment)
        .join(Hole)
        .join(Observatory)
        .switch(Assignment)
        .join(Design)
        .join(DesignToField)
        .join(Field)
        .where(
            Target.catalogid.in_(tuple(catalogids.keys()))
            & (AssignmentStatus.status == 1)
        )
        .tuples()
    )
    n, fields = (0, {})
    for obs, mjd, field_id in q:
        mjd = int(mjd)
        fields.setdefault((obs, mjd), set())
        fields[(obs, mjd)].add(field_id)
        n += 1

    console = Console()

    rich_table = RichTable(
        title=f"Exposures\n({n} obs/mjd/field combinations)",
        title_style="bold blue",
        show_header=True,
        header_style="bold cyan",
    )

    for field_name in (
        "#",
        "obs",
        "mjd",
        "exposure",
        "field",
        "fiber_id",
        "catalogid",
        "sdss_id",
    ):
        rich_table.add_column(field_name, justify="center")

    done = set()
    failed_categories = {}  # (observatory, mjd) -> category
    if output:
        # Nights written with -O are built exactly as `almanac --fibers` builds
        # them, night attributes included.
        write_night = _lookup_night_writer(output)

    i = 1
    with Live(rich_table, console=console, refresh_per_second=4) as live:
        for exposure in chain(*starmap(get_exposures, fields.keys())):
            key = (exposure.observatory, exposure.mjd)
            field_ids = fields[key]

            if output and key not in done:
                done.add(key)
                category = write_night(*key)
                if category:
                    failed_categories[key] = category

            if exposure.field_id in field_ids:
                for target in exposure.targets:
                    if target.catalogid in catalogids:
                        rich_table.add_row(
                            *list(
                                map(
                                    str,
                                    (
                                        i,
                                        exposure.observatory,
                                        exposure.mjd,
                                        exposure.exposure,
                                        exposure.field_id,
                                        target.fiber_id,
                                        target.catalogid,
                                        catalogids[target.catalogid],
                                    ),
                                )
                            )
                        )
                        i += 1
                        break
    if output:
        console.print(f"Updated {output} with:")
        for obs, mjd in done:
            if (obs, mjd) not in failed_categories:
                console.print(f"  - {obs}/{mjd}")
        for (obs, mjd), category in sorted(failed_categories.items()):
            console.print(f"  failed {obs}/{mjd} [{category}]")
        from almanac.exit_codes import exit_status_for

        status = exit_status_for(set(failed_categories.values()))
        if status:
            raise SystemExit(int(status))


@main.group()
def add(**kwargs):
    """Add new information to an existing Almanac file."""
    pass


def _get_sdss_ids(fp, obs, mjd):
    group = fp.get(f"raw/{obs}/{mjd}/fibers", [])
    sdss_ids = set()
    for config in group:
        sdss_ids.update(group[config]["sdss_id"][:])
    return sdss_ids


def _select_nights(fp, observatories, mjds=None):
    """
    The (observatory, mjd) pairs with an exposures table in an almanac file,
    restricted to the given observatories and, if given, MJDs.
    """
    mjds = None if mjds is None else {int(m) for m in mjds}
    nights = []
    for observatory in observatories:
        if f"raw/{observatory}" not in fp:
            continue
        for key in sorted(fp[f"raw/{observatory}"], key=int):
            mjd = int(key)
            if (mjds is None or mjd in mjds) and f"raw/{observatory}/{mjd}/exposures" in fp:
                nights.append((observatory, mjd))
    return nights


def _add_fibers_task(observatory, mjd, exposures, xmatch, overrides):
    """
    The `add fibers` worker: rewind exposures read back from the file to their
    header values, then run the same night pipeline as `almanac --fibers`
    (`apogee.process_night`: exposure overrides, fiber overrides,
    cross-match). Module level so that it pickles.
    """
    from almanac import apogee
    from almanac.overrides import rewind_exposure_overrides

    rewind_exposure_overrides(exposures)
    observatory, mjd, exposures, sequences, _missing = apogee.process_night(
        observatory, mjd, exposures, (), fibers=True, meta=xmatch, overrides=overrides
    )
    return observatory, mjd, exposures, sequences


@add.command()
@click.argument("input_path", type=str)
@click.option("--mjd", default=None, type=int,
              help="Modified Julian date. Use negative values to indicate relative to current MJD")
@click.option("--mjds", default=None, type=str,
              help="Comma-separated explicit MJD list (non-contiguous OK)")
@click.option("--mjd-start", default=None, type=int, help="Start of MJD range")
@click.option("--mjd-end", default=None, type=int, help="End of MJD range")
@click.option("--date", default=None, type=str, help="Date (e.g., 2024-01-15)")
@click.option("--date-start", default=None, type=str, help="Start of date range")
@click.option("--date-end", default=None, type=str, help="End of date range")
@click.option("--apo", is_flag=True, help="Apache Point Observatory nights")
@click.option("--lco", is_flag=True, help="Las Campanas Observatory nights")
@click.option("--output", "-O", "output_path", default=None, type=str,
              help="Copy INPUT to this new file and add the fiber mappings to the copy (INPUT is only read).")
@click.option("--in-place", is_flag=True, default=False,
              help="Rewrite the fiber mappings of INPUT itself. Required when --output is not given.")
@click.option("--no-x-match", is_flag=True, help="Do not cross-match targets with SDSS-V database")
@click.option("--no-overrides", is_flag=True,
              help="Do not apply the exposure overrides (the nights must have been built without them)")
@click.option("--full-apogee-id-lookup", is_flag=True,
              help="Build the full APOGEE ID -> SDSS ID lookup once up front (as for `almanac`)")
@click.option("--live-apogee-id-join", is_flag=True,
              help="Never read the APOGEE ID -> SDSS ID lookup table (as for `almanac`)")
@click.option("--processes", "-p", default=None, type=int,
              help="Worker processes (one observatory/MJD each). Default and meaning as for `almanac`.")
@click.option("--query-workers", default=None, type=int,
              help="Most worker processes that hold a database connection at once (as for `almanac`).")
def fibers(
    input_path, mjd, mjds, mjd_start, mjd_end, date, date_start, date_end, apo, lco,
    output_path, in_place, no_x_match, no_overrides, full_apogee_id_lookup,
    live_apogee_id_join, processes, query_workers, **kwargs,
):
    """Add fiber mappings (cross-matched) to an almanac file built without --fibers.

    \b
    Runs, for every selected night, the same code as `almanac --fibers`
    (exposure overrides, fiber overrides, sdss_id cross-match, fiber writer
    and night attributes), starting from the exposures stored in the file
    instead of the raw headers. The stored exposures are first returned to
    their header values, re-processed with the current override list, and
    compared with the stored exposures table and sequences: a night whose exposure columns would
    change (the override list changed since the night was built, or it was
    built with the other --no-overrides setting) is refused, listed at the
    end, and left untouched; rebuild it with `almanac`. Exposure columns and
    sequences are never rewritten. Every fiber table of an accepted night is
    replaced.

    \b
      almanac add fibers almanac.h5 --output almanac_fibers.h5
      almanac add fibers almanac.h5 --in-place --mjd 60285 --apo
    """
    import os
    import shutil
    import sys
    import functools
    import concurrent.futures
    import multiprocessing
    import h5py as h5
    from almanac import apogee, io, logger, parallel, utils
    from almanac.overrides import load_overrides, overrides_attrs

    if output_path and in_place:
        raise click.UsageError("Give --output or --in-place, not both.")
    if not output_path and not in_place:
        raise click.UsageError(
            "add fibers rewrites fiber tables: give --output NEW.h5 (INPUT is only "
            "read) or --in-place to modify INPUT."
        )
    if not os.path.exists(input_path):
        raise click.UsageError(f"{input_path} does not exist")
    if output_path:
        if os.path.exists(output_path):
            raise click.UsageError(f"--output {output_path} exists; choose a new path")
        if os.path.abspath(output_path) == os.path.abspath(input_path):
            raise click.UsageError("--output must differ from INPUT")

    observatories = utils.get_observatories(apo, lco)
    mjds_sel, *_ = utils.parse_mjds(
        mjd, mjd_start, mjd_end, date, date_start, date_end, return_nones=True, mjds=mjds
    )
    if not no_overrides:
        load_overrides()  # a malformed override file fails here, before any work
    override_attrs = overrides_attrs(not no_overrides)
    xmatch = not no_x_match

    with h5.File(input_path, "r") as fp:
        nights = _select_nights(fp, observatories, mjds_sel)
    if not nights:
        logger.warning(f"No nights with an exposures table selected in {input_path}")
        sys.exit(1)

    target = input_path
    if output_path:
        shutil.copyfile(input_path, output_path)
        target = output_path
        logger.info(f"Copied {input_path} to {output_path}; adding fiber mappings to the copy")

    processes_resolved = parallel.resolve_processes(processes, n_tasks=len(nights))
    query_workers_resolved = parallel.resolve_query_workers(query_workers)
    processes = processes_resolved[0]
    query_workers = query_workers_resolved[0]
    use_db_gate = xmatch and processes > 1 and processes > query_workers
    logger.info(f"Parallelism: {parallel.describe(processes_resolved, query_workers_resolved)}")

    apogee_id_source, lookup_attrs, full_lookup = apogee.prepare_cross_match(
        xmatch,
        live_apogee_id_join=live_apogee_id_join,
        full_apogee_id_lookup=full_apogee_id_lookup,
    )

    failed, refused, done = [], [], []

    def _write(observatory, mjd, exposures, sequences):
        # One write session per night, flushed and closed (see
        # `io.open_for_writing`): a killed run leaves a readable file.
        with io.open_for_writing(target) as fp:
            group = fp[f"raw/{observatory}/{mjd}/exposures"]
            differences = io.compare_exposures(group, exposures)
            differences += io.compare_sequences(fp, observatory, mjd, sequences)
            if differences:
                refused.append((observatory, mjd, differences))
                logger.error(
                    f"{observatory}/{mjd}: refused, the stored exposures differ from the "
                    f"re-processed ones ({'; '.join(differences[:3])}"
                    f"{'; ...' if len(differences) > 3 else ''}). Rebuild this night with `almanac`."
                )
                return
            io.delete_hdf5_entry(fp, f"raw/{observatory}/{mjd}/fibers")
            io.write_fibers(fp, observatory, mjd, exposures)
            io.write_night_attrs(
                fp, observatory, mjd, override_attrs, lookup_attrs,
                clear_stale_lookup_attrs=True,
            )
        done.append((observatory, mjd))

    # Read every night's stored exposures up front, with the file closed again
    # before any worker is forked (workers never hold its descriptor or lock).
    with h5.File(target, "r") as fp:
        task_args = [
            (observatory, mjd, io.read_exposures(fp, observatory, mjd), xmatch, not no_overrides)
            for observatory, mjd in nights
        ]

    def _process_and_write(observatory, mjd, get_result):
        """Run (or collect) one night and write it, classifying a failure."""
        from concurrent.futures.process import BrokenProcessPool

        try:
            result = get_result()
        except KeyboardInterrupt:
            raise
        except BrokenProcessPool as e:
            logger.error(f"Failed to add fibers for {observatory}/{mjd}: {e}")
            failed.append((observatory, mjd, "pool_broken", str(e)))
            return
        except Exception as e:
            logger.exception(f"Failed to add fibers for {observatory}/{mjd}: {e}")
            failed.append((observatory, mjd, _failure_category(e), str(e)))
            return
        try:
            _write(*result)
        except Exception as e:
            logger.exception(f"Failed to write the fibers of {observatory}/{mjd}: {e}")
            failed.append((observatory, mjd, "write_failed", str(e)))

    from almanac.signals import GracefulStop

    # SIGINT/SIGTERM/SIGHUP: kill the workers, keep the nights already
    # written, exit with 128 + the signal number (see `almanac.signals`).
    stop = GracefulStop()
    interrupted = None
    with stop:
        try:
            if processes > 1:
                mp_context = multiprocessing.get_context()
                db_gate = mp_context.BoundedSemaphore(query_workers) if use_db_gate else None
                if "almanac.database" in sys.modules:
                    try:
                        sys.modules["almanac.database"].database.close()
                    except Exception:
                        pass
                task_fn = (
                    functools.partial(parallel.run_task_releasing_db_slot, _add_fibers_task)
                    if use_db_gate else _add_fibers_task
                )
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=processes,
                    initializer=_pool_initializer,
                    initargs=(full_lookup, apogee_id_source, db_gate),
                    mp_context=mp_context,
                ) as pool:
                    try:
                        futures = {
                            pool.submit(task_fn, *args): args[:2] for args in task_args
                        }
                        for future in concurrent.futures.as_completed(futures):
                            observatory, mjd = futures[future]
                            _process_and_write(observatory, mjd, future.result)
                    except KeyboardInterrupt:
                        import signal as _signal
                        with stop.deferred():
                            for pid in list(pool._processes):
                                try:
                                    os.kill(pid, _signal.SIGKILL)
                                except OSError:
                                    pass
                            pool.shutdown(wait=False, cancel_futures=True)
                        raise
            else:
                for args in task_args:
                    _process_and_write(*args[:2], functools.partial(_add_fibers_task, *args))
        except KeyboardInterrupt as e:
            import signal as _signal
            interrupted = getattr(e, "signum", _signal.SIGINT)
            logger.warning(f"Stopped by signal {interrupted}; the nights already written are kept")

    logger.info(f"Added fiber mappings for {len(done)} of {len(nights)} nights in {target}")
    for observatory, mjd, differences in refused:
        logger.warning(f"  refused {observatory}/{mjd}: {'; '.join(differences)}")
    for observatory, mjd, category, message in failed:
        logger.warning(f"  failed {observatory}/{mjd} [{category}]: {message}")
    if interrupted is not None:
        sys.exit(128 + int(interrupted))
    # The highest exit status of the problems (almanac.exit_codes).
    from almanac.exit_codes import exit_status_for

    status = exit_status_for(
        {c for _, _, c, _ in failed} | ({"refused"} if refused else set())
    )
    if status:
        logger.warning(f"Exit status {int(status)} ({status.name})")
        sys.exit(int(status))


@add.command()
@click.argument("input_path", type=str)
@click.option(
    "--mjd",
    default=None,
    type=int,
    help="Modified Julian date to query. Use negative values to indicate relative to current MJD",
)
@click.option("--mjds", default=None, type=str,
              help="Comma-separated explicit MJD list (non-contiguous OK)")
@click.option("--mjd-start", default=None, type=int, help="Start of MJD range to query")
@click.option("--mjd-end", default=None, type=int, help="End of MJD range to query")
@click.option("--date", default=None, type=str, help="Date to query (e.g., 2024-01-15)")
@click.option(
    "--date-start", default=None, type=str, help="Start of date range to query"
)
@click.option("--date-end", default=None, type=str, help="End of date range to query")
@click.option("--apo", is_flag=True, help="Query Apache Point Observatory data")
@click.option("--lco", is_flag=True, help="Query Las Campanas Observatory data")
@click.option(
    "--query-workers",
    default=None,
    type=int,
    help=(
        "Number of parallel database query workers (default: the "
        "catalog_query_max_workers configuration setting). Each worker opens "
        "its own database connection, so this is also the number of database "
        "connections; keep it low over an SSH tunnel. 0 runs the queries in "
        "this process (debugging)."
    ),
)
@click.option(
    "--output", "-O", "output_path", default=None, type=str,
    help=(
        "Write /meta to this new file (a targets-only file) and open INPUT read-only. "
        "Without it /meta is written into INPUT (run that on a COPY)."
    ),
)
@click.option(
    "--since", default=None, type=str,
    help=(
        "A previously decorated almanac file or targets file: its /meta rows are copied "
        "and only the sdss_id it lacks are queried."
    ),
)
@click.option(
    "--restrict-to", default=None, type=str,
    help="Only build rows for sdss_id listed in this file (.npy, .h5 with /meta/sdss_id or /sdss_id, or text).",
)
@click.option(
    "--checkpoint-dir", default=None, type=str,
    help="Directory for per-batch checkpoints (default: <output or INPUT>.meta-checkpoint).",
)
@click.option("--batch-size", default=10_000, type=int, show_default=True,
              help="sdss_id per database batch (the unit of checkpointing).")
@click.option("--fresh", is_flag=True, help="Discard an existing checkpoint instead of resuming from it.")
@click.option("--keep-checkpoint", is_flag=True, help="Keep the checkpoint directory after the final write.")
@click.option("--retry-not-found", is_flag=True,
              help="With --since, query again the ids the previous build did not find.")
@click.option("--max-batch-attempts", default=8, type=int, show_default=True,
              help="Give up (keeping the checkpoint) after a batch fails this many times.")
@click.option("--retry-backoff", default=60.0, type=float, show_default=True,
              help="Base delay [s] before re-running failed batches (doubles each round, max 30 min).")
@click.option(
    "--extended", is_flag=True, default=False,
    help=(
        "Also write value-added columns after the default ones: SDSS_ID position "
        "(ra, dec, l, b, healpix), n_associated, unWISE, GLIMPSE, Bailer-Jones (2021) "
        "distances, Gaia DR3 synthetic photometry, Zhang, Green & Rix (2023) parameters "
        "and SDSS-IV APOGEE (DR17) targeting flags. 8 more queries per batch (slower)."
    ),
)
def metadata(
    input_path,
    mjd,
    mjds,
    mjd_start,
    mjd_end,
    date,
    date_start,
    date_end,
    apo,
    lco,
    query_workers,
    output_path,
    since,
    restrict_to,
    checkpoint_dir,
    batch_size,
    fresh,
    keep_checkpoint,
    retry_not_found,
    max_batch_attempts,
    retry_backoff,
    extended,
    **kwargs,
):
    """Add astrometry, photometry and targeting flags (/meta) for every sdss_id.

    \b
    Recommended workflow (the almanac file the pipeline reads is never written):
      cp almanac.h5 almanac_meta.h5
      almanac add metadata almanac_meta.h5 --query-workers 4      # decorate the COPY
    or write a targets-only file and leave INPUT untouched:
      almanac add metadata almanac.h5 --output targets.h5
    Incremental (only new sdss_id are queried):
      almanac add metadata new.h5 --output targets_new.h5 --since targets_old.h5
    Value-added columns (opt-in; the default columns are unchanged):
      almanac add metadata almanac.h5 --output targets_ext.h5 --extended

    Finished batches are checkpointed; re-running the same command resumes.
    The final /meta is written in one step (temporary file or group, then rename).

    INPUT must contain fiber mappings (built with --fibers). If none of the
    selected nights has them, nothing is written and the exit code is 1.
    """
    import sys
    from almanac import utils, config
    from almanac.metadata import build_metadata, MetadataBuildError

    observatories = utils.get_observatories(apo, lco)
    mjds, *_ = utils.parse_mjds(
        mjd, mjd_start, mjd_end, date, date_start, date_end, return_nones=True, mjds=mjds
    )
    from almanac import logger, parallel

    query_workers_resolved = parallel.resolve_query_workers(
        query_workers, config=config, allow_zero=True
    )
    query_workers = query_workers_resolved[0]
    logger.info(
        f"Parallelism: query workers={query_workers} "
        f"(from {query_workers_resolved[1]}); one database connection each"
        + (" (0: queries run in this process)" if query_workers == 0 else "")
    )
    try:
        build_metadata(
            input_path,
            output_path=output_path,
            since=since,
            restrict_to=restrict_to,
            observatories=observatories,
            mjds=mjds,
            query_workers=query_workers,
            batch_size=batch_size,
            checkpoint_dir=checkpoint_dir,
            fresh=fresh,
            keep_checkpoint=keep_checkpoint,
            retry_not_found=retry_not_found,
            max_batch_attempts=max_batch_attempts,
            retry_backoff=retry_backoff,
            extended=extended,
        )
    except MetadataBuildError as e:
        if e.exit_status is None:
            raise click.ClickException(str(e))
        # A batch failed after its retries (almanac.exit_codes).
        click.echo(f"Error: {e} (exit status {int(e.exit_status)}, {e.exit_status.name})", err=True)
        sys.exit(int(e.exit_status))


def _postprocess_chunk_worker(args):
    """
    Worker function for parallel postprocessing computations.

    This must be at module level for multiprocessing to pickle it.
    """
    import traceback
    import warnings
    import numpy as np

    task_type, chunk_indices, chunk_args = args

    # Suppress IERS auto-download warnings about polar motion data
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*IERS.*")
        warnings.filterwarnings("ignore", message=".*polar motion.*")

        try:
            if task_type == "bary":
                from almanac.postprocess import compute_barycentric_correction_chunk

                result = compute_barycentric_correction_chunk(*chunk_args)
                return (task_type, chunk_indices, result, None)
            elif task_type == "obs_meta":
                from almanac.postprocess import compute_observation_metadata_chunk

                result = compute_observation_metadata_chunk(*chunk_args)
                return (task_type, chunk_indices, result, None)
            elif task_type == "shadow":
                from almanac.postprocess import compute_shadow_heights_chunk

                result = compute_shadow_heights_chunk(*chunk_args)
                return (task_type, chunk_indices, result, None)
            else:
                return (
                    task_type,
                    chunk_indices,
                    None,
                    f"Unknown task type: {task_type}",
                )
        except Exception as e:
            # Return NaN results on error so processing can continue
            n = len(chunk_indices)
            if task_type == "bary":
                result = np.full(n, np.nan)
            elif task_type == "obs_meta":
                result = {
                    "moon_phase": np.full(n, np.nan),
                    "moon_separation": np.full(n, np.nan),
                    "airmass": np.full(n, np.nan),
                    "alt": np.full(n, np.nan),
                    "az": np.full(n, np.nan),
                }
            elif task_type == "shadow":
                result = np.full(n, np.nan)
            else:
                result = None
            error_msg = f"{task_type}: {e}\n{traceback.format_exc()}"
            return (task_type, chunk_indices, result, error_msg)

@main.command()
@click.argument("almanac_path", type=str)
@click.argument("observatory", type=str, default=None)
@click.argument("mjd", type=int, default=None)
@click.argument("exposure", type=int, default=None)
@click.argument("chip", type=str, default=None)
@click.pass_context
def flag(ctx, almanac_path, observatory, mjd, exposure, chip):
    """
    Not implemented: parses exposures and prints them; nothing is flagged.

    Exposures are flagged through almanac/etc/bad_exposures.csv and the
    not_science exposure overrides (the flagged_bad column). The intended
    interface takes a single exposure (or a chip of an exposure), or a list
    of exposures piped in from stdin:

    \b
    >> head bad_exposures.txt
    apo 57643 18 a
    apo 57643 19
    lco 59000 21 b

    >> cat bad_exposures.txt | almanac flag
    """
    def get_separator(line):
        for sep in (",", " ", "\t"):
            if line.count(sep) >= 2:
                return sep
        else:
            raise RuntimeError(f"Cannot determine separator for line: {line}")

    import sys
    if observatory is None:
        if sys.stdin.isatty():
            click.echo(flag.get_help(ctx))
            return

        inputs = []
        for i, line in enumerate(sys.stdin.readlines(), start=1):
            # comma or space
            sep = get_separator(line)
            parts = line.strip().split(sep)
            if len(parts) < 3:
                raise RuntimeError(
                    f"Expected <OBS>,<MJD>,<EXPOSURE>,[CHIP] "
                    f"but got invalid line {i}: {line}"
                )
            obs, mjd, exposure, *chip = parts
            obs = obs.lower()
            if obs not in ("apo", "lco"):
                raise RuntimeError(f"Invalid observatory '{obs}' in line {i}: {line}")
            try:
                mjd = int(mjd)
            except:
                raise RuntimeError(f"Invalid MJD '{mjd}' in line {i}: {line}")

            try:
                exposure = int(exposure)
                assert exposure >= 1
            except:
                raise RuntimeError(f"Invalid exposure '{exposure}' in line {i}: {line}")

            if len(chip) == 0 or (len(chip) == 1 and chip[0] == ""):
                chip = None
            elif chip is not None:
                chip = chip[0].lower()
                if chip not in "abc":
                    raise RuntimeError(f"Invalid chip '{chip}' in line {i}: {line}")
            inputs.append((obs, mjd, exposure, chip))
    else:
        inputs = [(observatory, mjd, exposure, chip)]

    #import h5py as h5
    #with h5.File(almanac_path, "r+") as fp:
    print(f"TODO: Flagging")
    print(inputs)



@main.command()
@click.argument("input_path", type=str)
@click.argument("output_prefix", type=str)
@click.option(
    "--processes", "-p", default=None, type=int,
    help=(
        "Worker processes. Default: the CPU budget (Slurm SLURM_CPUS_ON_NODE, "
        "else the CPU affinity, else all CPUs); negative values mean the same."
    ),
)
@click.option(
    "--limit", default=None, type=int, help="Limit number of spectra (for testing)"
)
def postprocess(input_path, output_prefix, processes, limit, **kwargs):
    """Post-process an existing Almanac file after reductions are complete."""

    if not input_path or not output_prefix:
        return

    import os

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"The file {input_path} does not exist.")

    import h5py as h5
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import numpy as np
    from glob import glob
    from collections import defaultdict

    from almanac.display import TaskDisplay
    from almanac.data_models.source import Source
    from almanac.data_models.spectrum import Spectrum
    from almanac.utils import adjusted_fiber_index_to_fiber_id
    from almanac.io import (
        _write_models_to_hdf5_group,
        get_default_array,
    )
    from almanac.postprocess import (
        finalize_radial_velocities,
        load_ar1d_unical_meta_batch,
    )

    from almanac import logger, parallel

    processes_resolved = parallel.resolve_processes(processes)
    processes = processes_resolved[0]
    logger.info(f"Parallelism: {parallel.describe(processes_resolved)}")

    outdir = os.path.dirname(os.path.abspath(input_path)) + "/../"
    output_spectra_path = f"{output_prefix}exposures.h5"
    output_stars_path = f"{output_prefix}stars.h5"

    data_dict = {}

    # Find other paths
    full_list_info_path = glob(f"{outdir}/arMADGICS/raw_*/full_list_info.h5")[0]
    arMADGICS_suffix = full_list_info_path.split("/")[-2][4:]
    arMADGICS_dir = f"{outdir}/arMADGICS/wu_th_{arMADGICS_suffix}/"
    arMADGICS_paths = glob(f"{arMADGICS_dir}/arMADGICS_out_*.h5")

    with TaskDisplay() as display:
        with ProcessPoolExecutor(max_workers=processes) as executor:

            tid_almanac = display.add_task("Loading Almanac data", total=1)
            tid_armadgics = display.add_task(
                "Loading arMADGICS scalars", total=len(arMADGICS_paths)
            )

            total = None
            with h5.File(full_list_info_path, "r") as full_list_info:
                keys = list(full_list_info.keys())
                tid_summary = display.add_task(
                    f"Reading {limit or len(full_list_info[keys[0]]):,} summary spectra",
                    total=len(keys),
                )
                for key in keys:
                    data_dict[key] = full_list_info[key][:]
                    display.advance(tid_summary)

            almanac_exposures = {}
            almanac_fibers = {}
            with h5.File(input_path, "r") as fp:
                n = sum(len(fp[f"raw/{o}"]) for o in ("apo", "lco") if f"raw/{o}" in fp)
                display.tasks[tid_almanac].total = n
                for obs, mjd, g in _iter_nights(fp):

                    key = (obs, int(mjd))

                    almanac_exposures[key] = {
                        k: v[:] for k, v in g["exposures"].items()
                    }
                    almanac_fibers[key] = {
                        int(cfg): {k: v[:] for k, v in g[f"fibers/{cfg}"].items()}
                        for cfg in g.get("fibers", {}).keys()
                    }
                    display.advance(tid_almanac)

            total = len(data_dict["sdss_id"])

            for path in arMADGICS_paths:
                basename = os.path.basename(path)
                # Extract key from filename: arMADGICS_out_<key>.h5
                key = basename[14:-3]
                with h5.File(path, "r") as fp:
                    if fp[key].ndim == 1:
                        data_dict[key] = fp[key][:]

                display.advance(tid_armadgics)

            star_fields = {**Source.model_fields, **Source.model_computed_fields}
            spectrum_fields = {
                **Spectrum.model_fields,
                **Spectrum.model_computed_fields,
            }

            tid_fidgeting = display.add_task("Fidgeting", total=6)

            # Collect all field names we'll encounter (for pre-allocation)
            t = {}
            for key, field_info in spectrum_fields.items():
                if field_info.alias is not None:
                    t[field_info.alias] = key
                    if field_info.alias in data_dict:
                        data_dict[key] = data_dict.pop(field_info.alias)

                data_dict.setdefault(key, get_default_array(field_info, total))

            # Submit the metadata job
            # metadata_future = io_executor.submit(
            #    load_metadata, metadata_pickle_path, data_dict["sdss_id"], t, m
            # )

            data_dict["observatory"] = np.array(data_dict["observatory"], dtype=str)
            data_dict["exposure"] = np.array(data_dict["exposure"], dtype=int)
            data_dict["mjd"] = np.array(data_dict["mjd"], dtype=int)

            display.advance(tid_fidgeting)

            # Stack and find unique rows
            stacked = np.column_stack(
                [
                    np.char.encode(data_dict["observatory"].astype(str), "utf-8"),
                    data_dict["mjd"].view("S8"),
                    data_dict["exposure"].view("S8"),
                ]
            )
            display.advance(tid_fidgeting)
            _, unique_idx, inverse_idx = np.unique(
                stacked, axis=0, return_index=True, return_inverse=True
            )
            sorted_idx = np.argsort(inverse_idx)
            _, counts = np.unique(inverse_idx, return_counts=True)
            grouped_indices = np.split(sorted_idx, np.cumsum(counts)[:-1])

            display.advance(tid_fidgeting)

            # Extract unique values
            unique_obs = data_dict["observatory"][unique_idx]
            unique_mjd = data_dict["mjd"][unique_idx]
            unique_exp = data_dict["exposure"][unique_idx]

            display.advance(tid_fidgeting)

            # Build lookups set and lookups_by_mjd dict
            lookups = list(zip(unique_obs, unique_mjd, unique_exp))
            lookups_by_ome = {
                tuple(row): idx for row, idx in zip(lookups, grouped_indices)
            }

            lookups_by_mjd = defaultdict(list)
            for obs, mjd, exp in lookups:
                lookups_by_mjd[mjd].append((obs, exp))

            display.advance(tid_fidgeting)

            n_exposures = len(lookups)
            tid_reduce_1d = display.add_task(
                f"Collecting ar1Dunical metadata ({n_exposures:,} exposures)",
                total=n_exposures + 1,
            )
            tid_prop_1d = display.add_task(
                "Processing ar1Dunical metadata", total=total
            )
            tid_fiber_lookups = display.add_task(
                "Processing fiber-level information", total=total
            )
            tid_chunking = display.add_task("Chunking", total=total)
            tid_vrad = display.add_task("Computing radial velocities", total=1)

            tid_shadow_height = display.add_task(
                "Computing shadow heights", total=total
            )
            tid_obs_meta = display.add_task(
                "Computing observation metadata (moon, airmass, alt/az)",
                total=1,
            )  # Updated later
            tid_meta = display.add_task("Processing source metadata", total=total)
            tid_write_spectra = display.add_task(
                f"Write spectra to {os.path.basename(output_spectra_path)}",
                total=len(spectrum_fields),
            )
            tid_star_unique = display.add_task("Identifying unique stars", total=1)
            tid_write_stars = display.add_task(
                f"Write stars to {os.path.basename(output_stars_path)}",
                total=len(star_fields),
            )

            batch_args = [
                (outdir, mjd, obs_exps) for mjd, obs_exps in lookups_by_mjd.items()
            ]
            display.advance(tid_fidgeting)

            display.advance(tid_reduce_1d)  # to show progress while the pool maps
            ar1D_unical_meta = {}
            for batch_result in executor.map(load_ar1d_unical_meta_batch, batch_args):
                ar1D_unical_meta.update(batch_result)
                display.advance(tid_reduce_1d, len(batch_result))

            if limit is not None:
                data_dict = {k: v[:limit] for k, v in data_dict.items()}

            # Assign ar1dunical meta
            n = len(lookups)
            display.tasks[tid_prop_1d].total = n
            for key, indices in lookups_by_ome.items():
                for k, v in ar1D_unical_meta[key].items():
                    data_dict[t.get(k, k)][indices] = v
                display.advance(tid_prop_1d)

            display.tasks[tid_fiber_lookups].total = len(lookups_by_ome)
            for (obs, mjd, exp), indices in lookups_by_ome.items():

                exp_data = almanac_exposures[(obs, mjd)]

                for k, v in exp_data.items():
                    try:
                        data_dict[t.get(k, k)][indices] = v[exp - 1]
                    except KeyError:
                        continue

                reference_id = max(
                    exp_data["config_id"][exp - 1], exp_data["plate_id"][exp - 1]
                )

                fiber_ids = adjusted_fiber_index_to_fiber_id(
                    data_dict["adjusted_fiber_index"][indices]
                )

                for k, v in almanac_fibers[(obs, mjd)][reference_id].items():
                    try:
                        data_dict[t.get(k, k)][indices] = v[fiber_ids - 1]
                    except KeyError:
                        continue

                display.advance(tid_fiber_lookups)

            # === PARALLEL COMPUTATION PHASE ===
            # Use a single ProcessPoolExecutor for all parallel computations.
            # This avoids the overhead of creating/destroying process pools multiple times.

            # Pre-compute shared arrays used by multiple parallel tasks
            ra, dec, mjd_mid_exposure = [data_dict[k] for k in ("ra", "dec", "mjd_mid_exposure")]
            observatory_arr = np.array(data_dict["observatory"]).astype(str)

            # Create validity mask for coordinates
            valid_mask = (
                np.isfinite(ra)
                & np.isfinite(dec)
                & np.isfinite(mjd_mid_exposure)
                & (ra >= 0)
                & (ra <= 360)
                & (dec >= -90)
                & (dec <= 90)
            )

            # Build chunks for parallel processing
            chunk_size = 5000
            chunks = []  # List of (task_type, chunk_indices, args)
            n_chunks = 0
            obs_chunk_indices = {}
            for obs in ("apo", "lco"):
                obs_mask = valid_mask & (observatory_arr == obs)
                if not np.any(obs_mask):
                    continue

                obs_chunk_indices[obs] = np.where(obs_mask)[0]

            display.tasks[tid_chunking].total = sum(
                map(len, obs_chunk_indices.values())
            )
            for obs in ("apo", "lco"):
                obs_indices = obs_chunk_indices.get(obs, None)
                if obs_indices is None:
                    continue

                # Split into chunks
                for chunk_start in range(0, len(obs_indices), chunk_size):
                    chunk_end = min(chunk_start + chunk_size, len(obs_indices))
                    chunk_indices = obs_indices[chunk_start:chunk_end]

                    # Barycentric correction chunk
                    chunks.append(
                        (
                            "bary",
                            chunk_indices,
                            (
                                ra[chunk_indices],
                                dec[chunk_indices],
                                mjd_mid_exposure[chunk_indices],
                                obs,
                            ),
                        )
                    )

                    # Observation metadata chunk (moon phase, separation, airmass, alt/az)
                    chunks.append(
                        (
                            "obs_meta",
                            chunk_indices,
                            (
                                ra[chunk_indices],
                                dec[chunk_indices],
                                mjd_mid_exposure[chunk_indices],
                                obs,
                            ),
                        )
                    )

                    # Shadow height chunk (uses JD, not MJD)
                    chunks.append(
                        (
                            "shadow",
                            chunk_indices,
                            (
                                ra[chunk_indices],
                                dec[chunk_indices],
                                mjd_mid_exposure[chunk_indices] + 2400000.5,
                                obs,
                            ),
                        )
                    )
                    display.advance(tid_chunking, len(chunk_indices))

            # Update task totals now that we know chunk count
            n_chunks = len(chunks) // 3  # Each obs has 3 chunk types
            display.tasks[tid_vrad].total = n_chunks
            display.tasks[tid_obs_meta].total = n_chunks
            display.tasks[tid_shadow_height].total = n_chunks

            # Process all chunks in parallel using module-level worker function
            errors = []
            futures = [
                executor.submit(_postprocess_chunk_worker, chunk) for chunk in chunks
            ]
            for future in as_completed(futures):
                try:
                    task_type, chunk_indices, result, error = future.result()
                    if error:
                        errors.append(error)
                    if task_type == "bary":
                        data_dict["v_barycentric_correction"][chunk_indices] = result
                        display.advance(tid_vrad)
                    elif task_type == "obs_meta":
                        data_dict["moon_phase"][chunk_indices] = result["moon_phase"]
                        data_dict["moon_separation"][chunk_indices] = result[
                            "moon_separation"
                        ]
                        data_dict["airmass"][chunk_indices] = result["airmass"]
                        data_dict["alt"][chunk_indices] = result["alt"]
                        data_dict["az"][chunk_indices] = result["az"]
                        display.advance(tid_obs_meta)
                    elif task_type == "shadow":
                        data_dict["shadow_height"][chunk_indices] = result
                        display.advance(tid_shadow_height)
                except Exception as e:
                    errors.append(f"Future exception: {e}")

            if errors:
                click.echo(f"Warning: {len(errors)} errors during parallel computation")

            # Finalize radial velocities (fast, no parallelization needed)
            rv_result = finalize_radial_velocities(
                data_dict["RV_pixoff_final"],
                data_dict["RV_pix_var"],
                data_dict["v_barycentric_correction"],
            )
            data_dict.update(rv_result)

            def callback(name):
                def inner(*args, **kwargs):
                    try:
                        display.advance(name)
                    except:
                        pass

                return inner

            # Metadata - load from HDF5 meta group
            with h5.File(input_path, "r") as fp:
                meta_group = fp["meta"]
                meta_sdss_ids = meta_group["sdss_id"][:]
                # Create lookup from sdss_id to index in meta arrays
                meta_lookup = {sid: idx for idx, sid in enumerate(meta_sdss_ids)}
                
                # Load all meta fields into memory
                meta_data = {key: meta_group[key][:] for key in meta_group.keys()}

            for i, sdss_id in enumerate(data_dict["sdss_id"]):
                meta_idx = meta_lookup.get(sdss_id)
                if meta_idx is None:
                    display.advance(tid_meta)
                    continue
                    
                for key, values in meta_data.items():
                    if key == "sdss_id":
                        continue
                    value = values[meta_idx]
                    if value is not None:
                        # Handle bytes -> str conversion for string fields
                        if isinstance(value, bytes):
                            value = value.decode('utf-8') if value else None
                        if value is not None and value != b'':
                            data_dict[t.get(key, key)][i] = value
                display.advance(tid_meta)

            with h5.File(output_spectra_path, "w", track_order=True) as fp:
                _write_models_to_hdf5_group(
                    spectrum_fields, data_dict, fp, callback=callback(tid_write_spectra)
                )

            # Now write per source
            display.tasks[tid_star_unique].total = len(data_dict)
            _, indices = np.unique(data_dict["sdss_id"], return_index=True)
            star_dict = {}
            for k, v in data_dict.items():
                star_dict[k] = v[indices]
                display.advance(tid_star_unique)

            with h5.File(output_stars_path, "w", track_order=True) as fp:
                _write_models_to_hdf5_group(
                    star_fields, star_dict, fp, callback=callback(tid_write_stars)
                )

    # Last check point for fields
    expected = set(spectrum_fields)
    actual = set(data_dict.keys())
    for key in actual - expected:
        click.echo(f"Warning: ignored field {key} in data_dict")
    for key in expected - actual:
        click.echo(f"Warning: missing field {key}")

PGDUMP_TABLES = (
    # APOGEE ID -> sdss_id lookup table built by the database maintainers
    # (read by `almanac.apogee` when present; see `apogee_id_lookup_view`).
    # Without it a restored copy falls back to the live four-table join.
    "catalogdb.mv_apogee_id_to_sdss_id",
    "catalogdb.sdss_id_flat",
    "catalogdb.sdss_id_to_catalog",
    "catalogdb.catalog",
    "catalogdb.version",
    "catalogdb.catalog_to_allstar_dr17_synspec_rev1",
    "catalogdb.allstar_dr17_synspec_rev1",
    "catalogdb.catalog_to_gaia_dr2",
    "catalogdb.gaia_dr3",
    "catalogdb.twomass_psc",
    "catalogdb.catalog_to_twomass_psc",
    "targetdb.target",
    "targetdb.carton_to_target",
    "targetdb.assignment",
    "targetdb.assignment_status",
    "targetdb.hole",
    "targetdb.observatory",
    "targetdb.design",
    "targetdb.design_to_field",
    "targetdb.field",
    "opsdb_apo.exposure",
    "opsdb_apo.exposure_flavor",
    "opsdb_lco.exposure",
    "opsdb_lco.exposure_flavor",
)

# Extra tables read by `almanac add metadata --extended` (from upstream a922dee;
# allstar_dr17_synspec_rev1 is already above). Dumped only with `pgdump --extended`:
# several are large (unwise, gaia_dr3_synthetic_photometry_gspc).
PGDUMP_EXTENDED_TABLES = (
    "catalogdb.sdss_id_stacked",
    "catalogdb.unwise",
    "catalogdb.glimpse",
    "catalogdb.bailer_jones_edr3",
    "catalogdb.gaia_dr3_synthetic_photometry_gspc",
    "catalogdb.gaia_stellar_parameters",
)


@main.command()
@click.argument("output_dir", type=str)
@click.option("--host", default=None, type=str, help="PostgreSQL host (overrides config)")
@click.option("--user", "-U", default=None, type=str, help="PostgreSQL user (overrides config)")
@click.option("--database", "-d", default="sdss5db", show_default=True, type=str, help="PostgreSQL database name")
@click.option("--extended", is_flag=True, default=False,
              help="Also dump the tables `add metadata --extended` reads (several are large).")
def pgdump(output_dir, host, user, database, extended=False, **kwargs):
    """Dump a fixed set of PostgreSQL tables to OUTPUT_DIR.

    Each table is dumped in pg_dump custom format (.dump). Tables that
    fail are skipped and reported at the end.
    """

    import os
    import subprocess

    from almanac import config

    pg_host = host or config.sdssdb.host
    pg_user = user or config.sdssdb.user

    os.makedirs(output_dir, exist_ok=True)
    tables = PGDUMP_TABLES + (PGDUMP_EXTENDED_TABLES if extended else ())

    click.echo(f"Found {len(tables)} tables")
    click.echo(f"Output directory: {output_dir}")
    click.echo("---")

    success = 0
    failed = 0

    for entry in tables:
        schema, _, table = entry.partition(".")
        out_path = os.path.join(output_dir, f"{schema}.{table}.dump")
        click.echo(f"Dumping {schema}.{table} ... ", nl=False)

        result = subprocess.run(
            [
                "pg_dump",
                "-h", pg_host,
                "-d", database,
                "-U", pg_user,
                "-Fc",
                "--no-owner",
                "--no-privileges",
                "-n", schema,
                "-t", f"{schema}.{table}",
                "-f", out_path,
            ],
            capture_output=True,
        )

        if result.returncode == 0:
            click.echo("OK")
            success += 1
        else:
            click.echo("FAILED (skipping)")
            if os.path.exists(out_path):
                os.remove(out_path)
            failed += 1

    click.echo("---")
    click.echo(f"Done: {success} succeeded, {failed} failed out of {len(tables)} tables")


@main.command()
@click.argument("output_dir", type=str)
@click.option("--host", "-h", required=True, type=str, help="Target PostgreSQL host")
@click.option("--database", "-d", required=True, type=str, help="Target PostgreSQL database name")
@click.option("--user", "-U", default=None, type=str, help="Target PostgreSQL user (overrides config)")
@click.option("--port", "-p", default=None, type=int, help="Target PostgreSQL port (overrides config)")
def pgrestore(output_dir, host, database, user, port, **kwargs):
    """Restore dumped PostgreSQL tables from OUTPUT_DIR into a target database.

    Reads the .dump files produced by `almanac pgdump` for the base table
    list (PGDUMP_TABLES; the `pgdump --extended` tables are not restored)
    and restores each one in turn. Schemas are created in the target
    database automatically if they do not already exist. Tables that are
    missing or cannot be restored are skipped and reported at the end.
    """

    import os
    import subprocess

    from almanac import config

    pg_user = user or config.sdssdb.user
    pg_port = str(port or config.sdssdb.port)

    if not os.path.isdir(output_dir):
        raise click.ClickException(f"Directory not found: {output_dir}")

    # Base args shared by both psql and pg_restore calls
    conn_args = ["-h", host, "-U", pg_user, "-p", pg_port, "-d", database]

    # Ensure all required schemas exist before restoring any tables.
    schemas = sorted({entry.partition(".")[0] for entry in PGDUMP_TABLES})
    click.echo(f"Creating schemas if needed: {', '.join(schemas)}")
    for schema in schemas:
        result = subprocess.run(
            ["psql", *conn_args, "-c", f"CREATE SCHEMA IF NOT EXISTS {schema};"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise click.ClickException(
                f"Failed to create schema '{schema}':\n"
                + result.stderr.decode(errors="replace")
            )

    click.echo(f"Restoring {len(PGDUMP_TABLES)} tables into {host}/{database}")
    click.echo("---")

    success = 0
    failed = 0
    missing = 0

    for entry in PGDUMP_TABLES:
        schema, _, table = entry.partition(".")
        dump_path = os.path.join(output_dir, f"{schema}.{table}.dump")

        if not os.path.exists(dump_path):
            click.echo(f"Restoring {schema}.{table} ... MISSING (skipping)")
            missing += 1
            continue

        click.echo(f"Restoring {schema}.{table} ... ", nl=False)

        result = subprocess.run(
            [
                "pg_restore",
                *conn_args,
                "--no-owner",
                "--no-acl",
                "-Fc",
                dump_path,
            ],
            capture_output=True,
        )

        if result.returncode == 0:
            click.echo("OK")
            success += 1
        else:
            click.echo("FAILED (skipping)")
            failed += 1

    click.echo("---")
    total = len(PGDUMP_TABLES)
    click.echo(
        f"Done: {success} succeeded, {failed} failed, {missing} missing "
        f"out of {total} tables"
    )


@main.group()
def config(**kwargs):
    """View or update configuration settings."""
    pass


@config.command()
def show(**kwargs):
    """Show all configuration settings"""

    from almanac import config, get_config_path
    from dataclasses import asdict

    click.echo(f"Configuration path: {get_config_path()}")
    click.echo(f"Configuration:")

    def _pretty_print(config_dict, indent=""):
        for k, v in config_dict.items():
            if isinstance(v, dict):
                click.echo(f"{indent}{k}:")
                _pretty_print(v, indent=indent + "  ")
            else:
                click.echo(f"{indent}{k}: {v}")

    _pretty_print(asdict(config), "  ")


@config.command
@click.argument("key", type=str)
def get(key, **kwargs):
    """Get a configuration value"""

    from almanac import config
    from dataclasses import asdict

    def traverse(config, key, provenance=None, sep="."):
        parent, *child = key.split(sep, 1)
        try:
            # TODO: Should we even allow dicts in config?
            if isinstance(config, dict):
                v = config[parent]
            else:
                v = getattr(config, parent)
        except (AttributeError, KeyError):
            context = sep.join(provenance or [])
            if context:
                context = f" within '{context}'"

            if not isinstance(config, dict):
                config = asdict(config)

            raise click.ClickException(
                f"No configuration key '{parent}'{context}. "
                f"Available{context}: {', '.join(config.keys())}"
            )

        provenance = (provenance or []) + [parent]
        return traverse(v, child[0], provenance) if child else v

    value = traverse(config, key)
    click.echo(value)


@config.command(hidden=True)
@click.argument("key")
@click.argument("value")
@click.pass_context
def update(ctx, key, value, **kwargs):
    """Update a configuration value (deprecated alias of `config set`)"""
    click.echo(click.style("Deprecated: use `almanac config set`", fg="yellow"), err=True)
    return ctx.invoke(_set, key=key, value=value)


@config.command(name="set")
@click.argument("key")
@click.argument("value")
def _set(key, value, **kwargs):
    """Set a configuration value (converted to the key's type)

    \b
    The value is converted to the type of the key (integer, number, text, or a
    list given as YAML or comma-separated); a value that does not fit is
    refused. Nested keys use dots, e.g. `almanac config set sdssdb.port 5432`.
    """

    from almanac import config, get_config_path, ConfigManager
    from almanac.config import ConfigValueError, set_config_value

    try:
        new_value = set_config_value(config, key, value)
    except ConfigValueError as e:
        raise click.ClickException(str(e))
    config_path = get_config_path()
    ConfigManager.save(config, config_path)
    click.echo(f"Updated configuration {key} to {new_value!r} in {config_path}")


@main.group()
def dump(**kwargs):
    """Dump data to a summary file"""
    pass


# almanac dump star[s] almanac.h5 output.fits
def check_paths_and_format(input_path, output_path, given_format, overwrite):
    import os
    import click

    if not os.path.exists(input_path):
        raise click.ClickException(f"Input path {input_path} does not exist")

    if os.path.exists(output_path) and not overwrite:
        raise click.ClickException(
            f"Output path {output_path} already exists. Use --overwrite to overwrite."
        )

    if given_format is None:
        if output_path.lower().endswith(".fits"):
            return "fits"
        elif output_path.lower().endswith(".csv"):
            return "csv"
        elif output_path.lower().endswith(".hdf5") or output_path.lower().endswith(
            ".h5"
        ):
            return "hdf5"
        else:
            raise click.ClickException(
                "Cannot infer output format from output path. Please specify --format"
            )
    return given_format


def _iter_nights(fp, observatories=("apo", "lco")):
    """(observatory, mjd, group) for every `raw/<obs>/<mjd>` in an almanac file
    (a file may hold one observatory only), in observatory and MJD order."""
    for observatory in observatories:
        if f"raw/{observatory}" not in fp:
            continue
        for mjd in sorted(fp[f"raw/{observatory}"], key=int):
            yield observatory, int(mjd), fp[f"raw/{observatory}/{mjd}"]


def _concatenate_column(parts):
    """Concatenate one column's per-table arrays; as strings if the types disagree."""
    import numpy as np

    try:
        return np.concatenate(parts)
    except (TypeError, ValueError):
        return np.concatenate([
            np.char.encode(p.astype(str)) if p.dtype.kind != "S" else p for p in parts
        ])


def _write_columns(output_path, output_format, data, overwrite):
    """Write {name: array} as a FITS/CSV table or as one HDF5 dataset per column."""
    import numpy as np

    if output_format == "hdf5":
        import h5py as h5

        with h5.File(output_path, "w", track_order=True) as fp:
            for name, values in data.items():
                fp.create_dataset(name, data=np.asarray(values))
    else:
        from astropy.table import Table

        Table(data=data).write(output_path, format=output_format, overwrite=overwrite)


@dump.command()
@click.argument("input_path", type=str)
@click.argument("output_path", type=str)
@click.option(
    "--format",
    "-f",
    default=None,
    type=click.Choice(["fits", "csv", "hdf5"]),
    help="Output format",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output file")
def stars(input_path, output_path, overwrite, format, **kwargs):
    """Create a star-level summary file (one row per sdss_id; FITS, CSV or HDF5)

    \b
    Counts the science (identified-star) fibers of the first configuration or
    plate table of each object sequence. Fibers without an sdss_id
    (sdss_id < 0: not matched, or not assigned one) are not counted (their
    number is logged); sdss_id 0 (a solar-system body placed by an override)
    is.
    """

    import h5py as h5
    import numpy as np
    from copy import deepcopy
    from collections import Counter
    from almanac.data_models.types import IDENTIFIED_STAR_CATEGORIES

    stars = {}
    default = dict(
        mjds_apo=set(),
        mjds_lco=set(),
        n_visits=0,
        n_visits_apo=0,
        n_visits_lco=0,
        n_exposures=0,
        n_exposures_apo=0,
        n_exposures_lco=0,
    )

    from almanac import logger

    output_format = check_paths_and_format(input_path, output_path, format, overwrite)
    n_without_sdss_id = 0
    with h5.File(input_path, "r") as fp:
        for observatory, mjd, group in _iter_nights(fp):

            is_object = group["exposures/image_type"][:].astype(str) == "object"
            fps = is_object * (group["exposures/config_id"][:] > 0)
            plate = is_object * (group["exposures/plate_id"][:] > 0)

            if not any(fps) and not any(plate) or "fibers" not in group:
                continue

            # fps era
            n_exposures_on_this_mjd = {}

            if any(fps):
                config_ids = Counter(group["exposures/config_id"][:][fps])
            elif any(plate):
                config_ids = Counter(group["exposures/plate_id"][:][plate])
            else:
                continue

            for config_id, n_exposures in config_ids.items():
                try:
                    config_group = group[f"fibers/{config_id}"]
                except KeyError:
                    print(
                        f"Warning couldnt get config {config_id} for {observatory} {mjd}"
                    )
                    continue

                identified = np.isin(
                    config_group["category"][:].astype(str),
                    IDENTIFIED_STAR_CATEGORIES,
                )
                # sdss_id >= 0 only: -1 means no sdss_id (pooling those
                # fibers would make a fake star -1); sdss_id 0 is a
                # solar-system body placed on a fiber by an exposure
                # override (Vesta; see almanac/etc/handplaced_targets.yaml).
                has_sdss_id = config_group["sdss_id"][:] >= 0
                n_without_sdss_id += int(np.sum(identified & ~has_sdss_id))
                ok = identified & has_sdss_id
                sdss_ids = config_group["sdss_id"][:][ok]
                catalogids = config_group["catalogid"][:][ok]
                for sdss_id, catalogid in zip(sdss_ids, catalogids):
                    stars.setdefault(sdss_id, deepcopy(default))
                    stars[sdss_id].setdefault(
                        "catalogid", catalogid
                    )  # this can change over time,... should we track that/
                    n_exposures_on_this_mjd.setdefault(sdss_id, 0)
                    n_exposures_on_this_mjd[sdss_id] += n_exposures

            for sdss_id, n_exposures in n_exposures_on_this_mjd.items():
                stars[sdss_id]["n_exposures"] += n_exposures
                stars[sdss_id][f"n_exposures_{observatory}"] += n_exposures
                stars[sdss_id]["n_visits"] += 1
                stars[sdss_id][f"n_visits_{observatory}"] += 1
                stars[sdss_id][f"mjds_{observatory}"].add(int(mjd))

        rows = []
        for sdss_id, meta in stars.items():
            stars[sdss_id].update(
                mjd_min_apo=min(meta["mjds_apo"]) if meta["mjds_apo"] else -1,
                mjd_max_apo=max(meta["mjds_apo"]) if meta["mjds_apo"] else -1,
                mjd_min_lco=min(meta["mjds_lco"]) if meta["mjds_lco"] else -1,
                mjd_max_lco=max(meta["mjds_lco"]) if meta["mjds_lco"] else -1,
            )
            stars[sdss_id].pop("mjds_apo")
            stars[sdss_id].pop("mjds_lco")
            rows.append(dict(sdss_id=sdss_id, **meta))

    if n_without_sdss_id:
        logger.warning(
            f"{n_without_sdss_id} science fibers without an sdss_id (sdss_id < 0) were not counted"
        )

    from astropy.table import Table

    t = Table(rows=rows)
    _write_columns(output_path, output_format, {name: t[name] for name in t.colnames}, overwrite)


@dump.command()
@click.argument("input_path", type=str)
@click.argument("output_path", type=str)
@click.option(
    "--format",
    "-f",
    default=None,
    type=click.Choice(["fits", "csv", "hdf5"]),
    help="Output format",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output file")
def visits(input_path, output_path, format, overwrite, **kwargs):
    """Not implemented: does nothing (no visit-level summary is written)."""

    pass


@dump.command()
@click.argument("input_path", type=str)
@click.argument("output_path", type=str)
@click.option(
    "--format",
    "-f",
    default=None,
    type=click.Choice(["fits", "csv", "hdf5"]),
    help="Output format",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output file")
def exposures(input_path, output_path, format, overwrite, **kwargs):
    """Create an exposure-level summary file"""

    import os
    import h5py as h5
    import numpy as np

    output_format = check_paths_and_format(input_path, output_path, format, overwrite)

    from almanac.data_models import Exposure

    fields = {**Exposure.model_fields, **Exposure.model_computed_fields}
    data = dict()
    for field_name, field_spec in fields.items():
        data[field_name] = []

    with h5.File(input_path, "r") as fp:
        for observatory, mjd, night in _iter_nights(fp):
            if "exposures" not in night:
                continue
            group = night["exposures"]
            for key in group.keys():
                data[key].extend(group[key][:])

    if output_format == "hdf5":
        from almanac.io import _write_models_to_hdf5_group

        fields = {**Exposure.model_fields, **Exposure.model_computed_fields}

        with h5.File(output_path, "w", track_order=True) as fp:
            _write_models_to_hdf5_group(fields, data, fp)
    else:
        from astropy.table import Table

        t = Table(data=data)
        t.write(output_path, format=output_format, overwrite=overwrite)


@dump.command()
@click.argument("input_path", type=str)
@click.argument("output_path", type=str)
@click.option(
    "--format",
    "-f",
    default=None,
    type=click.Choice(["fits", "csv", "hdf5"]),
    help="Output format",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output file")
def fibers(input_path, output_path, format, overwrite, **kwargs):
    """Create a fiber-level summary file (one row per fiber of every fiber table)

    \b
    Columns: `mjd` and `fiber_map_key` (the night and the fiber table, i.e.
    `raw/<observatory>/<mjd>/fibers/<fiber_map_key>`, the configuration or
    plate ID or a synthetic override table ID), then the FPSTarget and
    PlateTarget columns (`observatory` is the night's; columns a table does
    not have get their defaults).
    """

    import h5py as h5
    import numpy as np

    output_format = check_paths_and_format(input_path, output_path, format, overwrite)

    from almanac.data_models.fps import FPSTarget
    from almanac.data_models.plate import PlateTarget
    from almanac.io import get_default_array

    fields = {
        **FPSTarget.model_fields,
        **FPSTarget.model_computed_fields,
        **PlateTarget.model_fields,
        **PlateTarget.model_computed_fields,
    }

    parts = {"mjd": [], "fiber_map_key": [], **{name: [] for name in fields}}
    with h5.File(input_path, "r") as fp:
        for observatory, mjd, night in _iter_nights(fp):
            if "fibers" not in night:
                continue
            for key in night["fibers"]:
                table = night[f"fibers/{key}"]
                n = len(table["sdss_id"])
                parts["mjd"].append(np.full(n, mjd, dtype=np.int64))
                parts["fiber_map_key"].append(np.full(n, str(key).encode()))
                for name, spec in fields.items():
                    if name == "observatory":
                        values = np.full(n, observatory.encode(), dtype="S3")
                    elif name in table:
                        values = table[name][:]
                    else:
                        values = get_default_array(spec, n)
                    parts[name].append(values)

    data = {
        name: (
            _concatenate_column(columns) if columns
            else (np.zeros(0, dtype=np.int64) if name in ("mjd",)
                  else np.zeros(0, dtype="S1") if name == "fiber_map_key"
                  else get_default_array(fields[name], 0))
        )
        for name, columns in parts.items()
    }
    _write_columns(output_path, output_format, data, overwrite)


if __name__ == "__main__":
    main()
