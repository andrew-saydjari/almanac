"""
Exit statuses of the commands that build almanac files, in ONE place.

One code per kind of problem, numerically increasing with severity. A run in
which several kinds occur exits with the highest; its summary (and, for
``almanac --output``, the ``/run_summary`` group) still counts every kind.
0 is a clean run, 1 and 2 are click's (an error message, bad usage), and a
run stopped by a signal exits with 128 + the signal number.

Used by ``almanac`` (every code), ``almanac add fibers`` (refused, exception,
database error, write failed, pool broken), ``almanac add metadata``
(database error: a batch failed after its retries) and ``almanac lookup -O``
(exception, database error, write failed).
"""

from enum import IntEnum

__all__ = ["ExitStatus", "CATEGORY_EXIT_STATUS", "exit_status_for"]


class ExitStatus(IntEnum):
    """Exit status of a run (the highest that applies)."""

    #: Nothing went wrong (nights without data and ``--skip-existing`` skips
    #: are not problems).
    OK = 0

    #: The operations database could not be queried for a night's expected
    #: exposures; cross-match not requested. The night is written; only its
    #: missing-exposure check is incomplete (``/missing_exposures`` reason
    #: ``db_unavailable``).
    DB_UNAVAILABLE = 3

    #: The same, in a build that requested the cross-match (``--fibers``
    #: without ``--no-x-match``). The night is written.
    DB_UNAVAILABLE_CROSS_MATCH = 4

    #: ``add fibers``: a night was refused (its stored exposures or sequences
    #: differ from the re-processed ones) and left untouched.
    NIGHT_REFUSED = 5

    #: An exception while processing a night: a bad or unreadable input
    #: (plugmap without a PLUGMAPOBJ table, missing confSummary, unreadable
    #: raw file) or a bug. The night is not written.
    NIGHT_EXCEPTION = 6

    #: A database error that persisted after the retries (cross-match
    #: lookups; ``add metadata`` batches), or a cross-match without a
    #: database connection. The night (batch) is not written.
    DATABASE_ERROR = 7

    #: Writing a night to the output file failed.
    WRITE_FAILED = 8

    #: The worker process pool broke (e.g. a worker was killed); its
    #: outstanding nights are not written.
    POOL_BROKEN = 9


#: Category names (as recorded in ``/run_summary``) -> exit status.
CATEGORY_EXIT_STATUS = {
    "db_unavailable": ExitStatus.DB_UNAVAILABLE,
    "db_unavailable_cross_match": ExitStatus.DB_UNAVAILABLE_CROSS_MATCH,
    "refused": ExitStatus.NIGHT_REFUSED,
    "exception": ExitStatus.NIGHT_EXCEPTION,
    "database_error": ExitStatus.DATABASE_ERROR,
    "write_failed": ExitStatus.WRITE_FAILED,
    "pool_broken": ExitStatus.POOL_BROKEN,
}


def exit_status_for(categories) -> ExitStatus:
    """The highest exit status of the given categories (OK if none)."""
    return max(
        (CATEGORY_EXIT_STATUS[c] for c in categories), default=ExitStatus.OK
    )
