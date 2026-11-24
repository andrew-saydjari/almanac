# User Guide

How to use `almanac` to build the summary of APOGEE observations that the reduction
pipeline reads. The [CLI Reference](cli-reference.md) lists every option.

## Overview

For each observatory and night, `almanac`:

1. reads the headers of the raw APOGEE files (one row per exposure number; numbers with
   no file become `image_type = "missing"` rows) and asks the operations database which
   exposures it knows, to report missing ones;
2. applies the [exposure overrides](exposure-overrides.md);
3. builds the exposure **sequences**: runs of object exposures on the same
   field/plate/configuration (the visits), runs of arc lamp exposures, and runs of
   missing exposures;
4. with `--fibers`, reads the fiber-to-target mapping of each plate (SDSS-IV) or FPS
   configuration (SDSS-V) observed, and cross-matches the targets of the first exposure of
   each object sequence to an `sdss_id`.

It supports both the plate era and the robotic Fiber Positioning System (FPS) era (from
MJD 59423 at APO, 59809 at LCO).

## Basic Usage

```bash
almanac             # the current night, both observatories
almanac --apo       # Apache Point Observatory only
almanac --lco       # Las Campanas Observatory only
```

## Selecting nights

```bash
almanac --mjd 59300                                     # one night
almanac --mjd -1                                        # last night
almanac --date 2021-01-01                               # one night, by date
almanac --mjd-start 59300 --mjd-end 59310               # 11 nights, inclusive
almanac --mjd-start -30                                 # the last 30 nights to today
almanac --date-start 2021-01-01 --date-end 2021-12-31   # all of 2021
almanac --mjds 58011,59337,60000                        # just these three nights
```

Only one way of selecting nights may be given per run. Negative MJDs count back from the
current MJD, which advances at 12:00 UTC.

## Fiber mappings and the cross-match

```bash
almanac --mjd 60000 --fibers
```

This adds, per night, one fiber table per plate or configuration with the target
assignments, coordinates and catalog identifiers of every fiber, and the `sdss_id` of
each target. Keep the cross-match on (it is on unless `--no-x-match` is given); the
pipeline needs the `sdss_id`.

FPS fibers whose positioner was assigned a target but did not reach it are relabeled
`offtarget` and get no `sdss_id` (see [Data Formats](data-formats.md#off-target-fibers)).
Fibers that an exposure override says saw a hand-placed star or Vesta are rewritten
before the cross-match (see [Exposure Overrides](exposure-overrides.md)).

## Output and Verbosity

```bash
almanac                    # log messages only
almanac -v                 # live progress display
almanac -vv                # progress display, then every night's exposure table
```

```bash
almanac --output results.h5 --mjd-start -7                       # last week to HDF5
almanac -O monthly.h5 --date-start 2024-01-01 --date-end 2024-01-31
```

Running `almanac` again with the same output file replaces the nights processed and keeps
the others. See [Data Formats](data-formats.md) for the layout.

### Resuming long runs

```bash
almanac --mjd-start 59300 --mjd-end 60300 --fibers \
        --output survey.h5 --skip-existing
```

With `--skip-existing`, nights already in the output file are skipped, so if the run is
interrupted or some nights fail, re-run the same command. Transient database errors are
retried with backoff, and a night that fails is logged and skipped (the failed nights are
listed at the end and in `/run_summary`; the exit status is then 3). See the
[Configuration Guide](configuration.md) for the retry settings.

### Missing-exposure reporting

Every detected missing exposure is recorded in the run-level `/missing_exposures` table of
the output file, with a reason code distinguishing gaps on disk, database/disk
mismatches, and database outages. Review it before downstream processing; see
[Data Formats](data-formats.md#missing-exposures-report).

## Adding fiber mappings later

```bash
almanac add fibers survey.h5 --output survey_fibers.h5
```

adds fiber mappings to a file built without `--fibers`, running the same code as
`almanac --fibers` on the stored exposures (overrides and hand-placed targets included).
Use `--in-place` to modify the input instead. Nights whose stored exposures or sequences
would change under the current override list are refused; rebuild them with `almanac`.

## Adding catalog metadata

Once you have a file with fiber mappings, `almanac add metadata` builds a `meta` group
with astrometry, photometry and targeting flags for every cross-matched target (queried
once per unique `sdss_id`). Never write the file a pipeline is reading: decorate a copy or
write a targets file.

```bash
almanac add metadata survey.h5 --output survey_targets.h5
almanac add metadata survey.h5 --output survey_targets.h5 --query-workers 2   # gentler on the database
almanac add metadata new.h5 --output new_targets.h5 --since survey_targets.h5  # only new sdss_id
```

By default all nights in the file are used; restrict with `--mjd`, `--mjds`,
`--mjd-start`/`--mjd-end`, the date options, or `--apo`/`--lco`. If the file has no fiber
mappings for the selected nights, the command exits with status 1 without writing
anything. An interrupted build is resumed by re-running the same command.

> **Run `add metadata` database-local.** Throughput is dominated by database locality and
> cache state: a full-survey pass (about 4.7 million unique `sdss_id`) ran at about
> 660 ids/s cold (about 2 h 15 min) even with the database on the local network, against
> 13,000 to 26,000 ids/s on small warm slices. Through an SSH tunnel the same pass is
> impractically slow. Run it at Utah (or wherever is network-local to the SDSS database),
> and budget memory for the final assembly, when the finished batches are read back.

## Parallelism

```bash
almanac --mjd-start -30 -O month.h5                 # default: the CPU budget
almanac --processes 4 --mjd-start -30 -O month.h5   # 4 worker processes
almanac -p 8 --query-workers 2 --mjd-start -30      # 8 workers, at most 2 holding a database connection
```

Each worker processes one observatory/MJD pair; there are never more workers than pairs.
Without `--processes`, almanac uses `SLURM_CPUS_ON_NODE` inside a Slurm job, else the CPUs
the process may run on. On a shared login node, pass a small `--processes` explicitly.

## Exposure data

Each exposures table includes:
- **Basic**: observatory, MJD, exposure number, image type, file size
- **Identifiers**: plate, cartridge, field, design and configuration IDs
- **Instrument**: number of reads, lamp states, dither position, focus
- **Conditions**: seeing, observer comment
- **Flags and provenance**: `flagged_bad`, `chip_flags`, exposure override columns

## Fiber mapping data

**FPS (SDSS-V)**: `sdss_id`, `catalogid`, 2MASS designation, program, carton and
cadence, category, positioner, coordinates, and status flags (`assigned`, `on_target`,
`disabled`, `valid`, `decollided`).

**Plate (SDSS-IV)**: `sdss_id`, `catalogid`, 2MASS ID, target and object types,
coordinates, and plate-specific fields.

## Common Workflows

### Daily check

```bash
almanac -vv            # the current night, with details
almanac --apo -vv
```

### Building a release file

```bash
almanac --mjd-start 57618 --mjd-end 61000 --fibers -O allnights.h5 --skip-existing -v
almanac add metadata allnights.h5 --output allnights_targets.h5
```

### Comparing with and without the overrides

```bash
almanac --mjd 55755 --apo --fibers -O with.h5
almanac --mjd 55755 --apo --fibers --no-overrides -O without.h5
```

## Tips

- Use `-vv` and `almanac config set logging_level 10` for maximum detail.
- `almanac config show` prints the configuration path and database settings.
- Keep one output file per purpose and let `--skip-existing` resume it.

## Next Steps

- [Configuration Guide](configuration.md) for the settings
- [CLI Reference](cli-reference.md) for every command and option
- [Data Formats](data-formats.md) for the file layout and downstream readers
