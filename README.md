## almanac
`almanac` scrapes headers from raw image files and cross-matches those against the SDSS database to create a comprehensive summary of everything ever observed with an APOGEE instrument.

## Getting Started

Here are a few example cases of how `almanac` might be helpful:

List all exposures taken yesterday from either telescope:
```bash
almanac --mjd -1 -vv
```

Or just from Apache Point Observatory:
```bash
almanac --mjd -1 -vv --apo
```

Write out all exposures taken in the last month to `january.h5`:
```bash
almanac -o january.h5 --mjd-start -30
```

Write out all fiber observations during 2021, where we switched from plates to robotic fiber positioners:
```bash
almanac -vv --date-start 2021-01-01 --date-end 2021-31-12 --fibers
```

And it looks pretty, even when it warns you about missing exposures:

![](https://github.com/sdss/almanac/blob/83159e03632e3edbb45bb0c8de9810dec2dc49f1/docs/almanac-example-1.gif)


## Installation


### At Utah

If you want to use this at Utah:

```bash
module purge
module load almanac
```

> [!TIP]
> We recommend you manage your own Python environment, but if you don't have one set up at Utah then you can use `module load miniconda/3.8.5_astra`. 

### Anywhere else

`almanac` needs local disk access to raw APOGEE data frames. If you are going to run it somewhere else, you should set up a Globus transfer of raw APOGEE frames, and ensure your internet address is whitelisted to remotely access the SDSS database.

We recommend using `uv` to manage Python environments. Using `uv`, you can install `almanac` with:
```bash
uv add sdss-almanac
```

## Usage

Use `almanac` to see details on data taken today from both observatories, or specify the observatory:

```bash
almanac
almanac --apo # Apache Point Observatory
almanac --lco # Las Campanas Observatory
```

### Specifying a date

If you want a particular day, either use the ``--mjd`` or ``--date`` (UTC) flags:

```bash
almanac --mjd 59300
almanac --date 2021-01-01
```

You can use negative MJD values to indicate days relative to today:

```bash
almanac --mjd -1 # Yesterday
almanac --mjd -7 # Last week
```

You can also specify a range of days:

```bash
almanac --mjd-start 59300 --mjd-end 59310 # Give me these 10 days
almanac --date-start 2021-01-01 --date-end 2021-01-31 # Give me all of January 2021
```

Or an explicit list of days, which need not be contiguous:

```bash
almanac --mjds 58011,59337,60000 # Just these three days
```

### Fiber mappings

You can also use `almanac` to see the fiber mappings for a given plate (SDSS-IV) or FPS pointing (SDSS-V) by specifing the ``--fibers`` (or ``--fibres``) flag. This will give you the mapping of fibers to targets, and the target properties. 

```bash
almanac --mjd 60000 --fibres
```

The fiber mapping tables are cross-matched to the SDSS database to include the SDSS identifiers for each target. If you don't want to do this cross-match, you can use the ``--no-x-match`` flag. The ``--no-x-match`` flag is ignored if ``--fibers`` is not used.

### Verbosity

By default there is minimal output to the terminal. You can adjust the verbosity level using `-v`:
- `-v`: show progress display only
- `-vv`: show progress display and exposure metadata

In verbose mode you can see exposure information in the terminal, and additional per-fiber metadata is stored in the HDF5 files that `almanac` creates.

![](https://github.com/sdss/almanac/blob/e3f46c8ce66b88843de943ca31eec88d12be8f06/docs/almanac-example-2.gif)

### Outputs

You can write the outputs to a structured HDF5 file by specifying an output path with the ``--output`` (or ``-O``) flag. If the output path already exists, the default behaviour is to overwrite existing entries *only*. So if you run `almanac` once for MJD 60000 and output to a file, and then run it again for MJD 60001 and output to the same file, your file will have data for both MJDs. 

```bash
almanac --output /path/to/file.h5 # Append today's data to existing file
```

An example structure of the HDF5 file is below:

```
raw/apo/59300/exposures        # a data table of exposures
raw/apo/59300/sequences        # per image type, a Nx2 array of exposure numbers (inclusive) that form a sequence
raw/apo/59300/sequences/missing # Nx2 ranges of exposure numbers expected but not found on disk
raw/apo/59300/fibers/1         # a data table of fiber mappings, keyed by FPS
                               # configuration id (or plate id in the plate era)
missing_exposures              # run-level table of every missing exposure, with a reason
                               # (hole, trailing, db_no_file, file_no_db, db_unavailable)
```

For long runs, ``--skip-existing`` skips observatory/MJD pairs already present in the
output file, so an interrupted run can simply be re-run:

```bash
almanac --mjd-start 59300 --mjd-end 60300 -O big.h5 --fibers --skip-existing
```

Transient database errors are retried with backoff, and a night that fails outright is
recorded and skipped rather than killing the run (a list of failed nights is printed at
the end). See ``almanac config show`` for the retry and worker-count knobs.

## Adding fiber mappings to an existing file

If a file was built without `--fibers`, `almanac add fibers` adds the fiber mappings later.
It runs the same code as `almanac --fibers` (exposure overrides, hand-placed targets,
cross-match) on the exposures already in the file, so raw headers are not re-read:

```bash
almanac add fibers file.h5 --output file_fibers.h5     # INPUT only read
almanac add fibers file.h5 --in-place --mjd 60285 --apo
```

A night whose stored exposures would change under the current override list is refused
(rebuild it with `almanac`). See `docs/cli-reference.md`.

## Adding catalog metadata

Once you have an output file with fiber mappings, `almanac add metadata` builds a `meta/`
group of astrometry, photometry, and targeting flags for every matched target (queried
once per unique `sdss_id`). **Do not run it on the almanac file the pipeline is reading.**
Decorate a copy, or write a separate targets file:

```bash
cp /path/to/file.h5 /path/to/file_meta.h5
almanac add metadata /path/to/file_meta.h5 --query-workers 4    # decorate the COPY
almanac add metadata /path/to/file.h5 --output targets.h5       # input read-only
almanac add metadata new.h5 --output targets_new.h5 --since targets.h5  # only new sdss_id
almanac add metadata /path/to/file.h5 --output targets_ext.h5 --extended   # + value-added columns
```

`--extended` (opt-in) adds the SDSS_ID position with Galactic coordinates and a HEALPix
index, `n_associated`, unWISE and GLIMPSE photometry, Bailer-Jones et al. (2021)
distances, Gaia DR3 synthetic photometry, Zhang, Green & Rix (2023) parameters and the
SDSS-IV APOGEE (DR17) targeting bitmasks, after the default columns (adapted from
upstream sdss/almanac). Without it the output is unchanged.

Finished batches are checkpointed next to the output (`<output>.meta-checkpoint/`), so an
interrupted run is resumed by re-running the same command; the final `/meta` is written in
one step and carries provenance attributes (almanac version and commit, input file,
database host, query times, columns). See `docs/cli-reference.md`.

Note: `add metadata` is strongly sensitive to database locality — full-catalog passes
measured ~660 ids/s cold DB-local versus 13--26k ids/s warm, and are slow through an SSH
tunnel (about 0.27 s per cold id on one connection). Run it on a host local to the SDSS
database (e.g. at Utah) when you can.

If the file has no fiber mappings for the selected nights, `almanac add metadata` exits with
status 1 without writing anything: build the file with `--fibers` first.

## Configuration

You can view and change the `almanac` configuration settings through the `almanac config` interface. To view all current settings and to see the configuration file path:

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
