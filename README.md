## almanac
`almanac` scrapes headers from raw APOGEE image files and cross-matches them against the
SDSS database to build a summary of everything observed with an APOGEE instrument: one
table of exposures per observatory and night, the sequences of exposures that form visits,
and (with `--fibers`) the fiber-to-target mapping of each plate or FPS configuration with
an SDSS identifier (`sdss_id`) for every target.

This is the `andrew-saydjari/almanac` fork of [sdss/almanac](https://github.com/sdss/almanac),
version 0.5.3. It contains upstream 0.5.2 plus the fork's changes (see
[`CHANGELOG.rst`](CHANGELOG.rst)): explicit exposure overrides and hand-placed targets, the
off-target fiber relabeling, the APOGEE ID cross-match through the database maintainers'
lookup table, a resumable `almanac add metadata`, `almanac add fibers`, and Slurm-aware
parallelism defaults.

## Getting started

List all exposures taken yesterday at either observatory:
```bash
almanac --mjd -1 -vv
```

Or only at Apache Point Observatory:
```bash
almanac --mjd -1 -vv --apo
```

Write the exposures of the last 30 nights to `recent.h5`:
```bash
almanac -O recent.h5 --mjd-start -30
```

Write the exposures and fiber mappings of 2021 (when the survey switched from plates to
robotic fiber positioners):
```bash
almanac --date-start 2021-01-01 --date-end 2021-12-31 --fibers -O 2021.h5
```

It looks like this, even when it warns you about missing exposures:

![](https://github.com/sdss/almanac/blob/83159e03632e3edbb45bb0c8de9810dec2dc49f1/docs/almanac-example-1.gif)

## Installation

`almanac` needs local disk access to the raw APOGEE frames (and the plate, mapper and
`sdsscore` files; see the path settings in [`docs/configuration.md`](docs/configuration.md))
and access to the SDSS database. Python 3.11 or newer is required.

The `sdss-almanac` package on PyPI is upstream's and does not contain this fork's changes.
Install the fork from git, for example with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/andrew-saydjari/almanac.git
cd almanac
uv sync --frozen          # environment from uv.lock
uv run almanac --help
```

or into an existing environment with `uv pip install -e <checkout>`.

The database host, port and user come from `~/.almanac/config.yaml` (created with
defaults on first use, see `almanac config show`); the password comes from `~/.pgpass`
(or the file named by `PGPASSFILE`), as for any PostgreSQL client. Through an SSH tunnel,
set `sdssdb.host` to `localhost` and `sdssdb.port` to the local end of the tunnel.

## Usage

`almanac` with no options shows the current night at both observatories; select one with
`--apo` or `--lco`:

```bash
almanac
almanac --apo # Apache Point Observatory
almanac --lco # Las Campanas Observatory
```

### Specifying nights

A single night, by MJD or by date:

```bash
almanac --mjd 59300
almanac --date 2021-01-01
```

Negative MJDs count back from the current MJD (which advances at 12:00 UTC):

```bash
almanac --mjd -1 # last night
almanac --mjd -7 # a week ago
```

A range of nights (inclusive), or an explicit list that need not be contiguous:

```bash
almanac --mjd-start 59300 --mjd-end 59310             # these 11 nights
almanac --date-start 2021-01-01 --date-end 2021-01-31 # all of January 2021
almanac --mjds 58011,59337,60000                      # just these three nights
```

Only one of `--mjd`, `--mjds`, `--mjd-start`/`--mjd-end`, `--date`,
`--date-start`/`--date-end` may be given. A range with only a start runs to the current
MJD.

### Fiber mappings

`--fibers` (alias `--fibres`) adds the fiber-to-target mapping of each plate (SDSS-IV) or
FPS configuration (SDSS-V) observed, with the target properties, and cross-matches every
target to an `sdss_id`:

```bash
almanac --mjd 60000 --fibers
```

Always keep the cross-match on. (`--no-x-match` exists for debugging; its fiber tables
have `sdss_id = -1` everywhere.) Targets with an SDSS-V `catalogid` of 4204682057 or more
are matched by `catalogid`; the others by their designation: Gaia DR2 sources through
`catalog_to_gaia_dr2`, 2MASS-style designations through the DR17 allStar `apogee_id`
(read from the maintainers' lookup table `catalogdb.mv_apogee_id_to_sdss_id` when it is
available) and then the 2MASS point-source catalog. When an identifier reaches several
`sdss_id`, the best
`sdss_id_flat.rank` wins and, among equal ranks, the smallest `sdss_id`. See
[`docs/cli-reference.md`](docs/cli-reference.md).

### Verbosity

By default `almanac` only logs. `-v` shows a live progress display; `-vv` also prints the
exposure table of each night at the end.

![](https://github.com/sdss/almanac/blob/e3f46c8ce66b88843de943ca31eec88d12be8f06/docs/almanac-example-2.gif)

### Outputs

`--output` (or `-O`) writes an HDF5 file. An existing file is updated: the nights of this
run replace the same nights in the file and every other night is kept, so running once for
MJD 60000 and again for MJD 60001 with the same output gives a file with both.

```bash
almanac -O /path/to/file.h5 # add (or replace) the current night
```

The layout (details in [`docs/data-formats.md`](docs/data-formats.md)):

```
raw/apo/59300/exposures           # one row per exposure number 1..N (gaps filled with
                                  #   image_type "missing")
raw/apo/59300/sequences/objects   # Nx2 (first, last) exposure numbers: runs of object
                                  #   exposures on the same field/plate/configuration
raw/apo/59300/sequences/arclamps  # Nx2: runs of arc lamp exposures (darks included)
raw/apo/59300/sequences/missing   # Nx2: runs of missing exposure numbers
raw/apo/59300/fibers/<id>         # with --fibers: fiber table of a configuration (FPS)
                                  #   or plate, keyed by the exposure's config_id/plate_id
missing_exposures                 # run-level table of every missing exposure and why
meta                              # from `almanac add metadata`
```

The object sequences are the visits: the first exposure of each one is the exposure whose
fiber targets are cross-matched. ApogeeReduction.jl reads the exposures table and
`fibers/<config_id or plate_id>`; it does not read `sequences`.

For long runs, `--skip-existing` skips observatory/MJD pairs already in the output file,
so an interrupted run can simply be re-run:

```bash
almanac --mjd-start 59300 --mjd-end 60300 -O big.h5 --fibers --skip-existing
```

Transient database errors are retried with backoff, and a night that fails is logged and
skipped rather than ending the run. The run ends with a summary (also written to
`/run_summary` in the file) and exits with one status per kind of problem, the highest if
several (3-4 database unavailable, nights written; 6-9 nights not written; see the CLI
reference); nights without data and nights skipped by `--skip-existing` are not failures. See `almanac config show` for the retry settings.

### Parallelism

`--processes`/`-p` (default: the CPU budget, `SLURM_CPUS_ON_NODE` in a Slurm job, else the
CPU affinity, else all CPUs; never more than the number of nights) sets the worker
processes, one night each. `--query-workers` (default: the `catalog_query_max_workers`
setting, 4) caps how many of them hold a database connection at once.

### Exposure overrides and hand-placed targets

Exposures that the headers and plugmap/confSummary files describe wrongly are listed, with
evidence, in [`src/almanac/etc/exposure_overrides.yaml`](src/almanac/etc/exposure_overrides.yaml)
and applied on every run (`--no-overrides` switches them off for comparison). Fibers that
saw a hand-placed star (2011 commissioning tests) or the asteroid Vesta are rewritten
before the cross-match, in a per-exposure fiber table stored under a synthetic
`plate_id` of 1,000,000,000 or more. See [`docs/exposure-overrides.md`](docs/exposure-overrides.md).

## Adding fiber mappings to an existing file

If a file was built without `--fibers`, `almanac add fibers` adds the fiber mappings later.
It runs the same code as `almanac --fibers` (exposure overrides, hand-placed targets,
cross-match) on the exposures already in the file, so raw headers are not re-read:

```bash
almanac add fibers file.h5 --output file_fibers.h5     # INPUT only read
almanac add fibers file.h5 --in-place --mjd 60285 --apo
```

A night whose stored exposures or sequences would change under the current override list
is refused and left untouched (rebuild it with `almanac`). See
[`docs/cli-reference.md`](docs/cli-reference.md).

## Adding catalog metadata

Once you have an output file with fiber mappings, `almanac add metadata` builds a `meta`
group of astrometry, photometry and targeting flags for every matched target (queried
once per unique `sdss_id`). **Do not run it on the almanac file the pipeline is reading.**
Decorate a copy, or write a separate targets file:

```bash
cp /path/to/file.h5 /path/to/file_meta.h5
almanac add metadata /path/to/file_meta.h5 --query-workers 4    # decorate the COPY
almanac add metadata /path/to/file.h5 --output targets.h5       # input read-only
almanac add metadata new.h5 --output targets_new.h5 --since targets.h5  # only new sdss_id
almanac add metadata /path/to/file.h5 --output targets_ext.h5 --extended   # + value-added columns
```

`--extended` (opt-in) adds 79 value-added columns after the default ones: the SDSS_ID
position with Galactic coordinates and a HEALPix index, `n_associated`, unWISE and GLIMPSE
photometry, Bailer-Jones et al. (2021) distances, Gaia DR3 synthetic photometry, Zhang,
Green & Rix (2023) parameters and the SDSS-IV APOGEE (DR17) targeting bitmasks (adapted
from upstream). Without it the output is unchanged. It needs `healpy` (a dependency).

Finished batches are checkpointed next to the output (`<output>.meta-checkpoint/`), so an
interrupted run is resumed by re-running the same command; the final `meta` group is
written in one step and carries provenance attributes (almanac version and commit, input
file, database host, query times, columns). A hand-placed solar-system body
(`sdss_id` 0) gets a fixed row without a query.

`add metadata` is strongly sensitive to database locality: full-catalog passes measured
about 660 ids/s cold with the database on the local network, against 13,000 to 26,000
ids/s on small warm slices, and are slow through an SSH tunnel. Run it on a host local to
the SDSS database (e.g. at Utah) when you can.

If the file has no fiber mappings for the selected nights, `almanac add metadata` exits
with status 1 without writing anything: build the file with `--fibers` (or run
`almanac add fibers`) first.

## Configuration

View and change the settings with `almanac config`. To see all settings and the
configuration file path (`~/.almanac/config.yaml`):

```bash
almanac config show
```

### To get a single configuration value
```bash
almanac config get logging_level
```

### To set a configuration value
```bash
almanac config set logging_level 10
```

## Documentation

[`docs/`](docs/) holds the user guide, the full command reference, the configuration
reference, the data formats (every HDF5 group, dataset and attribute) and the exposure
overrides.
