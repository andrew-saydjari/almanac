# CLI Reference

Command-line reference for `almanac` 0.5.3. `almanac --help` and
`almanac <command> --help` print the same options.

```bash
almanac [OPTIONS]                  # build or show exposures (and fiber mappings)
almanac add fibers INPUT ...       # add fiber mappings to an existing file
almanac add metadata INPUT ...     # add catalog metadata (/meta) for every sdss_id
almanac config show|get|set ...    # view or change the configuration
almanac dump stars|exposures|fibers|visits INPUT OUTPUT
almanac lookup IDENTIFIER ...      # where an SDSS-V target was observed
almanac pgdump OUTPUT_DIR          # dump the database tables almanac reads
almanac pgrestore OUTPUT_DIR ...   # restore them into another database
almanac postprocess INPUT PREFIX   # upstream post-reduction summary (see below)
almanac flag ...                   # not implemented
```

The main command's options go before any subcommand and are ignored when a subcommand is
given.

## Main command

`almanac` reads the headers of the raw APOGEE files of each selected observatory and
night, fills gaps in the exposure numbering with `image_type = "missing"` rows, applies
the [exposure overrides](exposure-overrides.md), builds the exposure sequences and, with
`--fibers`, reads the fiber-to-target mapping of every plate or FPS configuration observed
and cross-matches each target to an `sdss_id`. With `--output` it writes all of this to an
HDF5 file ([Data Formats](data-formats.md)).

### Selecting nights

Give at most one of the following; without any, the current MJD is used. The current MJD
is the SDSS one: it advances at 12:00 UTC, so during an observing night it is that night's
MJD.

#### `--mjd <integer>`
One night. Negative values count back from the current MJD (`--mjd -1` is last night).

#### `--mjds <list>`
Comma-separated list of nights, which need not be contiguous; negative entries count back
from the current MJD.
- **Example**: `almanac --mjds 58011,59337,60000`

#### `--mjd-start <integer>`, `--mjd-end <integer>`
Inclusive range of nights; negative values count back from the current MJD. Without
`--mjd-end` the range ends at the current MJD; without `--mjd-start` it starts at MJD 0.
- **Example**: `almanac --mjd-start -30 --mjd-end -1`

#### `--date <YYYY-MM-DD>`
One night, by its date.

#### `--date-start <YYYY-MM-DD>`, `--date-end <YYYY-MM-DD>`
Inclusive range of dates.
- **Example**: `almanac --date-start 2024-01-01 --date-end 2024-01-31`

### Selecting observatories

#### `--apo`, `--lco`
Apache Point or Las Campanas Observatory only. With neither (or both), both observatories.

### Fiber mappings and the cross-match

#### `--fibers`, `--fibres`
Also read the fiber-to-target mapping (`confSummary` file in the FPS era, plugmap and
plateHoles files in the plate era) and write one fiber table per plate or configuration
(`raw/<obs>/<mjd>/fibers/<id>`). The table of a plate or configuration is the targets of
the first object exposure that used it, which is also the first exposure of an object
sequence.

#### `--no-x-match`
Do not cross-match the targets with the SDSS database: every `sdss_id` stays -1 and no
`apogee_id_lookup_*` night attributes are written. Only for debugging; production files
are always cross-matched.

#### How targets are cross-matched
The targets of the first exposure of every object sequence (and, on nights with exposure
overrides, the first object exposure of every fiber table) are matched:

- a target whose `catalogid` is 4204682057 or more is matched by `catalogid`
  (`catalogdb.sdss_id_flat`);
- otherwise its designation is used: a `GAIA_DR2 <source_id>` designation through
  `catalog_to_gaia_dr2`; any other designation, with its `2MASS-J`, `2MASS`, `2M`, `J`,
  `APG-J` or `AP` prefix removed, first through the DR17 allStar `apogee_id`, then through
  the 2MASS point-source catalog (`twomass_psc`).

Every query keeps rank-1 rows of `sdss_id_flat`. When an identifier still reaches several
`sdss_id` (for example two catalogids, each with a rank-1 `sdss_id`), the smallest
`sdss_id` is used, on every path. Fibers with category `unplugged` or `offtarget` are not
matched (`sdss_id = -1`); a hand-placed solar-system body keeps the `sdss_id` its override
set (0).

**APOGEE ID lookup source.** By default almanac queries only the APOGEE IDs each night
needs (1,000 identifiers per query, remembered for the rest of the process). If the
relation named by the configuration key `apogee_id_lookup_view` (default
`catalogdb.mv_apogee_id_to_sdss_id`) exists, is readable and is not empty, it is read
instead of the live four-table join (`sdss_id_flat`, `sdss_id_to_catalog`,
`catalog_to_allstar_dr17_synspec_rev1`, `allstar_dr17_synspec_rev1`). Its
`Last refreshed: YYYY-MM-DD` comment, if any, is logged, and a warning is logged if one of
those source tables was analyzed after that date. Identifiers the table misses are looked
up again with the live join; a match found that way is logged as a warning that the table
is stale. The database maintainers built `catalogdb.mv_apogee_id_to_sdss_id` on 2026-11-18
as an ordinary table (despite the `mv_` prefix); almanac reads it with SQL, so no sdssdb
upgrade is needed. It is a snapshot and must be rebuilt whenever one of the four source
tables is updated. It is included in `almanac pgdump`. Each night records the source it
used in its `apogee_id_lookup_*` attributes ([Data Formats](data-formats.md#night-attributes)).

#### `--full-apogee-id-lookup`
Build the lookup for every DR17 allStar entry once, before the workers start, and give it
to all of them, instead of querying per night. One heavy query (tens of minutes warm,
hours with a cold database cache) when the lookup table is not available. Recorded as
`apogee_id_lookup_mode = "full"`.

#### `--live-apogee-id-join`
Never read the lookup table; always use the live join.

### Exposure overrides

#### `--no-overrides`
Do not apply `almanac/etc/exposure_overrides.yaml` (to see what the overrides change).
The nights record `exposure_overrides = "disabled"`. See
[Exposure Overrides](exposure-overrides.md).

### Output

#### `--output <path>`, `-O <path>`
Write to this HDF5 file. An existing file is updated: for every night processed in this
run, the exposures table and sequences are replaced and, with `--fibers`, the fiber tables
of the plates and configurations observed are written (replacing tables with the same
key; tables with other keys are left in place). Other nights are kept. The run-level
`/missing_exposures` table is rewritten, keeping the entries of nights not processed in
this run.

#### `--skip-existing`
With `--output`: skip every observatory/MJD pair that already has a
`raw/<obs>/<mjd>/exposures` group in the file. Re-running an interrupted or partly failed
run with the same command then processes only the missing nights.
- **Example**: `almanac --mjd-start 59300 --mjd-end 60300 --fibers -O big.h5 --skip-existing`

#### `-v`, `--verbosity`
Repeatable. No flag: log messages only. `-v`: a live full-screen progress display. `-vv`:
also print the exposure table of every night at the end.

### Parallelism

#### `--processes <integer>`, `-p <integer>`
Worker processes, each processing one observatory/MJD pair.
- **Default**: the CPU budget: `SLURM_CPUS_ON_NODE` in a Slurm job (or the CPU affinity if
  smaller), else `len(os.sched_getaffinity(0))`, else `os.cpu_count()`. Never more than
  the number of observatory/MJD pairs, so a one-night run is done in the main process.
  Negative values mean the default; `0` or `1` run in the main process. The resolved
  numbers and their source are logged (`Parallelism: ...`).

#### `--query-workers <integer>`
The most worker processes that hold a database connection at once. Every night needs the
database (expected exposures from the operations database; with `--fibers`, the
cross-match). When `--processes` is larger, a worker takes a slot at its first query of a
night and closes its connection and frees the slot when that night is done; the other
workers keep reading files meanwhile.
- **Default**: the `catalog_query_max_workers` [configuration setting](configuration.md) (4)
- **Example**: `almanac -p 32 --query-workers 4 --fibers --mjd-start 60800 --mjd-end 60830`

### Fault tolerance and exit status

- Transient database connection errors are retried with exponential backoff
  (`database_retry_attempts`, `database_retry_backoff`).
- A night that fails is logged and skipped; the run goes on. Every run ends with a
  summary (always logged): the number of nights requested, skipped by `--skip-existing`,
  with data, without data, failed, and built without the operations database, then the
  failed nights with their category and error, and a reminder to re-run with
  `--skip-existing`. With `--output` the same is written to the
  [`/run_summary`](data-formats.md#run-summary) group of the file.
- If the operations database cannot be reached for a night's expected exposures, the
  night is still written and `/missing_exposures` records a `db_unavailable` row for it.
- An invalid night selection (for example two selection options) raises an error (exit
  status 1 or 2); a malformed override file fails before any night is processed.

Exit statuses (`almanac.exit_codes.ExitStatus`): one per kind of problem, increasing with
severity. A run in which several kinds occur exits with the **highest**; the summary and
`/run_summary` count every kind. 1 and 2 are click's (an error message, bad usage), and a
run stopped by a signal exits with 128 + the signal number.

<!-- audit:table exit_status -->
| Name | Code | Meaning | Commands |
|------|------|---------|----------|
| `OK` | 0 | Nothing went wrong (nights without data and `--skip-existing` skips are not problems) | all |
| `DB_UNAVAILABLE` | 3 | The operations database could not be queried for a night's expected exposures, cross-match not requested; the night is written, only its missing-exposure check is incomplete (`db_unavailable`) | `almanac` |
| `DB_UNAVAILABLE_CROSS_MATCH` | 4 | The same, with the cross-match requested (`--fibers` without `--no-x-match`); the night is written | `almanac` |
| `NIGHT_REFUSED` | 5 | A night was refused (stored exposures or sequences differ from the re-processed ones) and left untouched | `add fibers` |
| `NIGHT_EXCEPTION` | 6 | An exception while processing a night (a plugmap without a `PLUGMAPOBJ` table, a missing confSummary, an unreadable raw file, or a bug); the night is not written | `almanac`, `add fibers`, `lookup -O` |
| `DATABASE_ERROR` | 7 | A database error that persisted after the retries (cross-match lookups; `add metadata` batches), or a cross-match without a database connection; the night (batch) is not written | `almanac`, `add fibers`, `lookup -O`, `add metadata` |
| `WRITE_FAILED` | 8 | Writing a night to the output file failed | `almanac`, `add fibers`, `lookup -O` |
| `POOL_BROKEN` | 9 | The worker process pool broke (e.g. a worker was killed); its outstanding nights are not written | `almanac`, `add fibers` |

What can happen to a requested night of `almanac`:

| Night | In the file | Logged | Category in `/run_summary` | Exit status |
|-------|-------------|--------|----------------------------|-------------|
| Built normally (holes and missing raw files are recorded in `/missing_exposures`) | yes | summary count | (none) | 0 |
| No exposure files on disk | no (only `/missing_exposures` rows, e.g. `db_no_file`) | summary count | (none) | 0 |
| Skipped by `--skip-existing` (already in the file) | unchanged | summary count | earlier rows kept | 0 |
| Operations database not reachable for the expected exposures | yes (missing-exposures check incomplete) | warning, listed at the end | `db_unavailable` | 3, or 4 with the cross-match |
| Exception while processing | no | error + traceback, listed at the end | `exception` | 6 |
| Database error after the retries, or cross-match without a connection | no | as above | `database_error` | 7 |
| Writing the night failed | no | as above | `write_failed` | 8 |
| Worker process pool broke | no | as above | `pool_broken` | 9 |
| Not reached because the run was stopped by a signal | no | summary count (`not processed`) | (none) | 128 + signal |

Without `--output` the same statuses apply. The two categories that cannot be told apart
reliably are kept together: `NIGHT_EXCEPTION` covers bad input and bugs alike.

### Stopping a run

SIGINT (Ctrl-C), SIGTERM (`kill PID`, and what a Slurm time limit sends first) and SIGHUP
stop a run cleanly: the worker processes are killed, the nights already processed are
written (at most `max(4, 2 x --processes)` are queued), the missing-exposures table and run
summary are written, and the exit status is 128 + the signal number (130, 143, 129).
SIGINT works for background jobs too (a shell starts `&` jobs with SIGINT ignored; almanac
handles it anyway); SIGHUP is left ignored under `nohup`. Re-run the same command with
`--skip-existing` to continue.

`kill -9` (SIGKILL, or the out-of-memory killer) cannot be handled. The output file is
opened only while a night is written and is flushed and closed after every night, so it
stays readable, up to the last night written; the worker processes die with the main
process. Nights written by a killed run whose missing-exposure rows were not recorded yet
are listed in `/missing_exposures_pending`, and the next run with `--skip-existing`
processes them again instead of skipping them. (A kill while a night that replaces one
already in the file is being written can damage that night; rebuild it.)

## `almanac add fibers`

```bash
almanac add fibers INPUT (--output NEW.h5 | --in-place) [OPTIONS]
```

Adds fiber mappings, cross-matched, to an almanac file built without `--fibers`. For every
selected night it runs the same pipeline as `almanac --fibers` (`apogee.process_night`:
exposure overrides, fiber overrides with hand-placed targets and synthetic fiber-table
IDs, the `sdss_id` cross-match) and writes with the same writers (`io.write_fibers`,
`io.write_night_attrs`). Its input is the exposures table already in the file (raw headers
are not re-read), first returned to header values (`plate_id`/`config_id` from
`original_plate_id`/`original_config_id`; override columns cleared).

**Exposure columns and sequences are never rewritten.** After re-processing, the exposures
and sequences are compared with the stored ones, column by column in the stored
representation. A night that would differ (the override list changed since the night was
built, the night was built with the other `--no-overrides` setting, a file from before the
overrides on a night that now has one, or raw files appeared or disappeared so that
`chip_flags` changes) is refused, left untouched, and listed at the end (exit status 5):
rebuild it with `almanac`. Every fiber table of an accepted night is replaced, and the
night attributes are rewritten (stale `apogee_id_lookup_*` attributes are removed with
`--no-x-match`).

**Safety:** give exactly one of `--output NEW.h5` (INPUT is copied to NEW, which must not
exist, and only read) or `--in-place`. Never run it in place on a file a pipeline is
reading.

**Options**: the night selection options and `--apo`/`--lco` as for `almanac` (default:
every night in INPUT with an exposures table); `--output`/`-O`, `--in-place`;
`--no-x-match`; `--no-overrides` (the nights must have been built without overrides);
`--full-apogee-id-lookup`; `--live-apogee-id-join`; `--processes`/`-p` and
`--query-workers` (as for `almanac`). There is no British-spelling alias for `add fibers`.

**Exit status**: 1 if no night is selected; otherwise the highest of the
[exit statuses](#fault-tolerance-and-exit-status) of its problems (5 refused, 6 exception,
7 database error, 8 write failed, 9 pool broken); 128 +
the signal number when stopped by SIGINT, SIGTERM or SIGHUP (the nights already written are
kept; each night is written, flushed and closed on its own, so a killed run leaves a
readable file).

## `almanac add metadata`

```bash
almanac add metadata INPUT [--output TARGETS.h5] [OPTIONS]
```

Builds a `meta` group, one row per unique positive `sdss_id` found in the fiber tables of
the selected nights: catalog cross-match identifiers, Gaia DR3 astrometry and photometry,
2MASS photometry, and SDSS-V targeting flags (`sdss5_target_flags`). A hand-placed
solar-system body (`sdss_id` 0, see [Exposure Overrides](exposure-overrides.md)) gets a
fixed row without a query: `lead = "solar_system"`, every catalog column at its default
(-1, empty, or NaN). Other non-positive `sdss_id` are skipped.

If none of the selected nights has fiber mappings, it exits with status 1 and writes
nothing (no `/meta`, no output file, no checkpoint); nights without fiber mappings are
counted in a warning and skipped. A batch that still fails after `--max-batch-attempts` stops the build with exit status 7
(`DATABASE_ERROR`; the checkpoint is kept, re-run to resume); other errors exit with 1.

**Never write the almanac file a pipeline reads.** Either decorate a copy, or write a
targets-only file:

```bash
cp results.h5 results_meta.h5
almanac add metadata results_meta.h5 --query-workers 4          # decorate the COPY in place
almanac add metadata results.h5 --output targets.h5              # INPUT opened read-only
almanac add metadata new.h5 --output targets_new.h5 --since targets.h5   # incremental
```

A targets file written with `--output` holds only `/meta`, `/meta_not_found` and
`/meta_carton_pks` (and the root attribute `written_by`).

**Resumable.** The `sdss_id` are queried in batches (`--batch-size`, default 10,000). Each
finished batch is written to `<output or INPUT>.meta-checkpoint/batch_NNNNNN.h5`
(temporary name, then rename) and `progress.json` is updated. If the run is killed, or a
batch still fails after the per-query reconnect retries, re-running the same command
continues from the finished batches; failed batches are re-run after a backoff
(`--retry-backoff`, doubling, at most 30 min) up to `--max-batch-attempts` times. A
checkpoint made for a different `sdss_id` set, batch size, or `--extended` setting is
refused (`--fresh` discards it). The checkpoint directory is removed after the final write
unless `--keep-checkpoint`.

**Atomic final write.** `/meta` is assembled once all batches are done. With `--output` the
file is written under a temporary name and renamed; in place, the group is written as
`meta__tmp` and renamed to `meta`. A reader never sees a half-written `/meta`. The ids that
were queried but not found are kept in `meta_not_found/sdss_id`. Every carton the database
returned is kept in `meta_carton_pks/{sdss_id, carton_pk}` (one row per pair), because
`sdss5_target_flags` can only encode the cartons the installed `sdss_semaphore` knows (the
others are listed in the `unknown_carton_pks` attribute). The provenance attributes of
`/meta` are listed in [Data Formats](data-formats.md#catalog-metadata-meta).

**Options**:
- Night selection (`--mjd`, `--mjds`, `--mjd-start`/`--mjd-end`, `--date`,
  `--date-start`/`--date-end`; default: every night in the file) and `--apo`/`--lco`.
- `--query-workers <integer>`: parallel database query workers (default: the
  `catalog_query_max_workers` configuration setting). Each worker opens its own database
  connection, so this is also the number of connections. `0` runs the queries in this
  process. This command reads no raw files, so it has no `--processes`.
- `--output`, `-O <file>`: write `/meta` to this new file; INPUT is opened read-only.
  Without it `/meta` is written into INPUT.
- `--since <file>`: copy the `/meta` rows of a previously decorated file or targets file
  and query only the `sdss_id` it lacks (ids it recorded as not found are not queried
  again unless `--retry-not-found`).
- `--restrict-to <file>`: only build rows for these `sdss_id` (`.npy`, `.h5` with
  `/meta/sdss_id` or `/sdss_id`, or text with one id per line).
- `--checkpoint-dir <dir>`, `--batch-size <n>` (10000), `--fresh`, `--keep-checkpoint`,
  `--max-batch-attempts <n>` (8), `--retry-backoff <s>` (60).
- `--extended`: also write 79 value-added columns after the default ones (adapted from
  upstream sdss/almanac `a922dee`): SDSS_ID position (`ra`, `dec`, `l`, `b`, `healpix` at
  nside 128, RING), `n_associated`; unWISE W1/W2 fluxes, magnitudes and flags; GLIMPSE
  4.5 µm; Bailer-Jones et al. (2021) distances; Gaia DR3 synthetic photometry (GSPC);
  Zhang, Green & Rix (2023) parameters (`zgr_*`); SDSS-IV APOGEE (DR17) targeting bitmasks
  (`sdss4_apogee_*`). Off by default: without it `/meta` has exactly the default columns.
  It runs 8 more queries per batch: measured 2026-11-19 with one query worker, the extra
  queries cost about 47 to 77 ms per `sdss_id` on a cold database cache, against about 5
  to 9 ms per `sdss_id` for the default queries on a warm cache, and about 1.4 to 2.3 ms
  per `sdss_id` when warm. With `--since`, rows of a file without the extended columns are
  queried again rather than copied. A value-added query that fails makes the batch fail
  and be retried (it is never skipped into default values). Upstream's 15 reddening
  columns (`ebv*`) are not written: nothing fills them. `/meta` gets the attributes
  `extended` and `extended_column_groups` in this mode only. Needs `healpy` (a declared
  dependency).
- (`--p` was removed: it had been ignored for a long time; use `--query-workers`.)

**Run it database-local.** Throughput is strongly sensitive to database locality and
cache state. Measured on a full-survey file (about 4.7 million unique `sdss_id`) with the
database on the local network: about 660 ids/s (cold catalog cache; about 2 h 15 min),
against 13,000 to 26,000 ids/s on small warm slices. Over an SSH tunnel the same pass is
impractically slow. Run `add metadata` on a host local to the SDSS database (e.g. at Utah),
and budget memory for the final assembly (the finished batches are read back into memory
for the single final write).

## `almanac config`

### `almanac config show`
Print the configuration file path (`~/.almanac/config.yaml`) and every setting.

### `almanac config get <key>`
Print one setting. Nested settings use dot notation (`almanac config get sdssdb.host`);
a section name prints the whole section.

### `almanac config set <key> <value>`
Set one setting and save the file. Nested settings use dot notation; a whole section
cannot be set at once. The value is converted to the type of the key (integer, number,
text, or a list as YAML or comma-separated); a value that does not fit is refused (exit
status 1) and nothing is saved. `almanac config update <key> <value>` is a hidden,
deprecated alias.

```bash
almanac config set logging_level 10
almanac config set sdssdb.host localhost
almanac config set sdssdb.port 5432
```

See [Configuration](configuration.md) for every key.

## `almanac dump`

```bash
almanac dump stars|exposures|fibers INPUT OUTPUT [--format fits|csv|hdf5] [--overwrite]
```

Write a summary table of an almanac file. The format is taken from `--format`/`-f` or the
extension of OUTPUT (`.fits`, `.csv`, `.h5`/`.hdf5`); an existing OUTPUT is refused
without `--overwrite`.

- `dump stars`: one row per `sdss_id` from the fiber tables of the object exposures with
  an identified-star category (`science`, `veto_location_boss`, `veto_location_apogee`,
  `standard_apogee`, `standard_boss`, `open_fiber`; see
  `almanac.data_models.types.IDENTIFIED_STAR_CATEGORIES`): `catalogid`, `n_visits`
  (nights; also per observatory), `n_exposures` (object exposures, flagged ones included;
  also per observatory), and `mjd_min_*`/`mjd_max_*` per observatory (-1 if none). The
  hand-placed solar-system body (`sdss_id` 0) is counted. Fibers without an `sdss_id`
  (`sdss_id < 0`: not matched, or not assigned one) are not counted; their number is
  logged. HDF5 output has one dataset per column.
- `dump exposures`: every row of every `raw/<obs>/<mjd>/exposures` table (all `Exposure`
  columns).
- `dump fibers`: every row of every fiber table: `mjd` and `fiber_map_key` (the night and
  the table, `raw/<observatory>/<mjd>/fibers/<fiber_map_key>`), then the union of the FPS
  and plate columns, with defaults where a table lacks a column; `observatory` is the
  night's for every row.
- `dump visits`: not implemented (does nothing).

A file may hold one observatory only, and nights without fiber tables are skipped.

## `almanac lookup`

```bash
almanac lookup IDENTIFIER [IDENTIFIER ...] [--output FILE]
```

Finds where SDSS-V targets were observed: each IDENTIFIER is an `sdss_id` or a
`catalogid`; every catalogid of the matching `sdss_id` is looked up in `targetdb`
(assignments with status 1) and the matching exposures are printed as a table (FPS era
only). With `--output`, every night found is written to FILE as `almanac --fibers` writes
it (cross-matched, with overrides, with the night attributes), but without
`/missing_exposures`.

## `almanac pgdump` and `almanac pgrestore`

```bash
almanac pgdump OUTPUT_DIR [--host HOST] [--user USER] [--database sdss5db] [--extended]
almanac pgrestore OUTPUT_DIR --host HOST --database DB [--user USER] [--port PORT]
```

`pgdump` runs `pg_dump -Fc` for a fixed list of tables (`almanac.cli.PGDUMP_TABLES`: the
`catalogdb`, `targetdb` and `opsdb_apo`/`opsdb_lco` tables almanac reads, including
`catalogdb.mv_apogee_id_to_sdss_id`) into `<schema>.<table>.dump` files. `--extended` adds
the tables `add metadata --extended` reads (`PGDUMP_EXTENDED_TABLES`; several are large).
Host and user default to the configuration; there is no port option (`pg_dump` uses its
default or `PGPORT`). Failed tables are skipped and counted.

`pgrestore` creates the schemas if needed and runs `pg_restore` for each file of the base
list (`PGDUMP_TABLES`); the `--extended` tables are not restored. Missing and failed
files are skipped and counted.

## `almanac postprocess`

```bash
almanac postprocess INPUT OUTPUT_PREFIX [--processes N] [--limit N]
```

Upstream's post-reduction summary: from an almanac file decorated with `add metadata`
and the arMADGICS outputs found relative to it (`<dir of INPUT>/../arMADGICS/...`), it
writes `<OUTPUT_PREFIX>exposures.h5` and `<OUTPUT_PREFIX>stars.h5` with barycentric
corrections, moon and airmass metadata, and shadow heights. The fork's pipelines do not
use it. `--processes` defaults to the CPU budget, as for `almanac`.

## `almanac flag`

Not implemented: it parses the exposures given on the command line or on standard input
(`<obs> <mjd> <exposure> [chip]`), prints them, and flags nothing. Exposures are flagged
through `src/almanac/etc/bad_exposures.csv` and the `not_science` exposure overrides
(see `flagged_bad` in [Data Formats](data-formats.md)).

## Examples

```bash
# Last night at both observatories, with the exposure tables
almanac --mjd -1 -vv

# A month of exposures and fiber mappings, written to a file
almanac --date-start 2024-01-01 --date-end 2024-01-31 --fibers -O 2024_01.h5

# Large multi-year run; safe to interrupt and re-run
almanac --mjd-start 59300 --mjd-end 60300 --fibers -O survey.h5 --skip-existing -v

# Then catalog metadata, into a separate targets file
almanac add metadata survey.h5 --output survey_targets.h5 --query-workers 4

# Fiber mappings for a file built without --fibers
almanac add fibers exposures.h5 --output exposures_fibers.h5

# What do the overrides change on one night?
almanac --mjd 55755 --apo --fibers -O with.h5
almanac --mjd 55755 --apo --fibers --no-overrides -O without.h5
```

## Environment variables

- `SLURM_CPUS_ON_NODE`: the default number of `--processes` inside a Slurm job.
- `PGPASSFILE`, `PGPORT` and the other libpq variables: read by the PostgreSQL client
  (the database password normally comes from `~/.pgpass`).
- `HOME`: the configuration file is `$HOME/.almanac/config.yaml`.

## Shell completion

`almanac` is a [click](https://click.palletsprojects.com/) application, so click's
completion works, e.g. for bash:

```bash
eval "$(_ALMANAC_COMPLETE=bash_source almanac)"
```
