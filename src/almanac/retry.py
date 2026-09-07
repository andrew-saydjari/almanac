"""
Retry helpers for database queries over flaky connections (e.g., an SSH tunnel).

Long almanac runs from a remote site talk to the SDSS operations database
through a single SSH tunnel. A momentarily dropped connection surfaces as a
``peewee.OperationalError`` or ``peewee.InterfaceError``; without a retry the
whole run dies. These helpers retry such failures with exponential backoff,
forcing a reconnect between attempts.
"""

from functools import wraps
from time import sleep

from peewee import InterfaceError, OperationalError

from almanac import config, logger

# Exceptions that indicate a (possibly transient) connection-level problem.
# peewee wraps the underlying driver (psycopg2/psycopg) exceptions in these.
RETRYABLE_EXCEPTIONS = (OperationalError, InterfaceError)


def reconnect() -> bool:
    """
    Close and re-open the (shared) sdssdb database connection.

    :returns:
        Whether the database is connected after the attempt.
    """
    from almanac.database import database

    try:
        database.close()
    except Exception:
        pass
    try:
        return database.connect(reuse_if_open=True)
    except Exception as e:
        logger.warning(f"Database reconnect attempt failed: {e}")
        return False


def retry_on_database_error(fn=None, *, attempts=None, backoff=None, max_delay=60.0):
    """
    Decorator that retries a function on transient database connection errors.

    Between attempts the database connection is closed and re-opened, and the
    delay grows exponentially (``backoff * 2**(attempt - 1)``, capped at
    ``max_delay`` seconds).

    Usable bare (``@retry_on_database_error``) or with arguments
    (``@retry_on_database_error(attempts=3, backoff=1.0)``).

    :param attempts:
        Total number of attempts (default: ``config.database_retry_attempts``).
    :param backoff:
        Base delay in seconds (default: ``config.database_retry_backoff``).
    :param max_delay:
        Maximum delay between attempts, in seconds.
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            n_attempts = int(
                attempts
                if attempts is not None
                else getattr(config, "database_retry_attempts", 5)
            )
            base_delay = float(
                backoff
                if backoff is not None
                else getattr(config, "database_retry_backoff", 2.0)
            )
            for attempt in range(1, n_attempts + 1):
                try:
                    return f(*args, **kwargs)
                except RETRYABLE_EXCEPTIONS as e:
                    if attempt == n_attempts:
                        logger.error(
                            f"Database error in {f.__name__} persisted after "
                            f"{n_attempts} attempts: {e}"
                        )
                        raise
                    delay = min(base_delay * 2 ** (attempt - 1), max_delay)
                    logger.warning(
                        f"Database error in {f.__name__} "
                        f"(attempt {attempt}/{n_attempts}): {e}. "
                        f"Retrying in {delay:.0f} s after reconnecting."
                    )
                    sleep(delay)
                    reconnect()

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
