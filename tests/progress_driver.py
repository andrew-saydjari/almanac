"""
Driver for tests/test_progress.py: runs one almanac command in a fresh
process, so the test can give it a pseudo-terminal or a pipe as its
standard output and error.

    python tests/progress_driver.py meta DIR [QUERY_WORKERS] [SLEEP] [extra build_metadata args as k=v]
    python tests/progress_driver.py build DIR [SLEEP] [extra almanac options]

``meta`` runs `almanac.metadata.build_metadata` (fake catalog query, no
database) with a `TaskDisplay` that decides for itself whether to draw.
``build`` runs the `almanac` main command on MJDs without data (no database
or raw files are touched); with SLEEP > 0 each night takes that long.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SLEEP = 0.0


def slow_fake_query(ids, suffix=""):
    """Fake catalog query run in a worker: prints and logs a line, then sleeps."""
    from tests.test_metadata_build import fake_query

    if suffix == "_1":
        print(f"worker {os.getpid()} says hello from batch{suffix}", flush=True)
        import logging

        logging.getLogger().warning(f"worker log line from batch{suffix}")
    time.sleep(SLEEP)
    return fake_query(ids, suffix)


FAIL_ONCE = {}


def flaky_fake_query(ids, suffix=""):
    """Fails the first time batch 2 is queried (in-process runs only)."""
    if suffix == "_2" and not FAIL_ONCE.get(suffix):
        FAIL_ONCE[suffix] = True
        raise ConnectionError("fake connection lost")
    return slow_fake_query(ids, suffix)


def run_meta(directory, workers="0", sleep="0", *extra):
    global SLEEP
    SLEEP = float(sleep)
    from tests.test_metadata_build import make_almanac
    import almanac.logger  # noqa: F401  (the colorlog handler, as in the CLI)
    from almanac.metadata import build_metadata
    from almanac.progress import TaskDisplay

    path = os.path.join(directory, "almanac.h5")
    if not os.path.exists(path):
        make_almanac(path)
    kwargs = dict(
        output_path=os.path.join(directory, "targets.h5"),
        query_workers=int(workers),
        batch_size=10,
        keep_checkpoint=True,
        retry_backoff=0.0,
        query_batch=slow_fake_query,
    )
    for item in extra:
        key, value = item.split("=", 1)
        if key == "query_batch":
            kwargs[key] = globals()[value]
        else:
            kwargs[key] = type(kwargs.get(key, ""))(value) if key in kwargs else value
    with TaskDisplay() as bars:
        build_metadata(path, progress=bars, **kwargs)


def run_build(directory, sleep="0", *options):
    sleep = float(sleep)
    import almanac.apogee as apogee
    from almanac.cli import main

    if sleep > 0:
        def get_almanac_data(observatory, mjd, *args, **kwargs):
            time.sleep(sleep)
            return observatory, mjd, [], {}, []

        apogee.get_almanac_data = get_almanac_data
    args = ["--mjd-start", "51000", "--mjd-end", "51002", "--apo", "-p", "0",
            "--output", os.path.join(directory, "out.h5"), *options]
    main.main(args, prog_name="almanac")


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    {"meta": run_meta, "build": run_build}[command](*rest)
