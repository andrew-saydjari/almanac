# Parallelism benchmark

`bench_parallelism.py` times `almanac` runs over a small grid of
`--processes` x `--query-workers` and records, per run, the wall time and the
peak (and mean) number of database connections the run's process tree held
at once. Connections are counted from `/proc` (established TCP sockets to the
database port), so the benchmark adds no load to the database. Every run uses
`--fibers` with cross-matching on (never `--no-x-match`).

Two cases:

- **daily**: one night (`--daily-mjd`). One observatory/MJD pair always runs
  in the main process (one connection), whatever `-p` is, so it is repeated
  (`--daily-repeats`, default 2) to show the database-cache effect rather than
  swept.
- **bulk piece**: a few nights (`--bulk-mjd-start`..`--bulk-mjd-end`). One
  untimed-in-spirit warm-up run (smallest grid point, labelled `bulk_warmup`;
  `--no-warmup` skips it), then the grid. Points with `query_workers >
  processes` are skipped (each worker holds at most one connection, so they
  behave like `query_workers == processes`).

The script refuses grids larger than `--max-processes`/`--max-query-workers`
(default 8). Keep it small on a shared login node.

## Running it (e.g. at Utah, where the database is local)

```bash
D=<a new dated scratch folder>
mkdir -p $D && cd $D
git clone --branch parallelism-defaults https://github.com/andrew-saydjari/almanac.git almanac
uv venv --python 3.12 venv
VIRTUAL_ENV=venv uv pip install -e almanac
# the database host/port/user come from ~/.almanac/config.yaml, the password
# from ~/.pgpass (or PGPASSFILE)
nohup venv/bin/python almanac/benchmarks/parallelism/bench_parallelism.py \
    --almanac $D/venv/bin/almanac --obs apo \
    --daily-mjd 60855 --bulk-mjd-start 60840 --bulk-mjd-end 60845 \
    --processes 1,2,4 --query-workers 1,2,4 --max-processes 4 \
    --outdir $D/results_apo > $D/bench.out 2>&1 &
```

Outputs in `--outdir`: `results.md` (table), `results.csv`, `results.json`
(with host and UTC start/end), one `<case>_p<P>_q<Q>.log` per run (the
almanac log, which starts with the resolved `Parallelism:` line). The
almanac HDF5 outputs are deleted unless `--keep-outputs`; their night count
and the number of fibers with `sdss_id > 0` are recorded so that a run that
lost its cross-match is visible.

`--db-port` defaults to the `sdssdb.port` in `~/.almanac/config.yaml`
(5432 at Utah; the local end of the tunnel elsewhere).
