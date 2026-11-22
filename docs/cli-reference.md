# CLI Reference

Complete command-line interface reference for `almanac`.

## Main Command

```bash
almanac [OPTIONS] [COMMAND]
```

The main `almanac` command queries APOGEE observational data. When run without subcommands, it performs data queries based on the specified options.

## Global Options

### Date and Time Selection

#### `--mjd <integer>`
Query specific Modified Julian Date.
- Positive values: Absolute MJD (e.g., `59300`)
- Negative values: Relative to current MJD (e.g., `-1` for yesterday)
- **Example**: `almanac --mjd 59300`, `almanac --mjd -7`

#### `--mjd-start <integer>`
Start of MJD range for queries.
- **Example**: `almanac --mjd-start 59300 --mjd-end 59310`

#### `--mjd-end <integer>`
End of MJD range for queries (inclusive).
- **Example**: `almanac --mjd-start -30 --mjd-end -1`

#### `--mjds <list>`
Comma-separated list of explicit MJDs to query. The list does not need to be contiguous.
- Useful for testbeds, reprocessing selected nights, or filling gaps
- Also accepted by `almanac add metadata`
- **Example**: `almanac --mjds 58011,59337,60000`

#### `--date <YYYY-MM-DD>`
Query specific calendar date (UTC).
- **Format**: ISO date format (YYYY-MM-DD)
- **Example**: `almanac --date 2021-01-01`

#### `--date-start <YYYY-MM-DD>`
Start of calendar date range.
- **Example**: `almanac --date-start 2021-01-01 --date-end 2021-01-31`

#### `--date-end <YYYY-MM-DD>`
End of calendar date range (inclusive).
- **Example**: `almanac --date-start 2024-01-01 --date-end 2024-12-31`

### Observatory Selection

#### `--apo`
Query Apache Point Observatory data only.
- **Example**: `almanac --apo --mjd -1`

#### `--lco`
Query Las Campanas Observatory data only.
- **Example**: `almanac --lco --date 2024-01-01`

### Data Options

#### `--fibers`, `--fibres`
Include fiber-to-target mappings in output.
- **Example**: `almanac --mjd 60000 --fibers`

#### `--no-x-match`
Skip cross-matching targets with SDSS database.
- Only effective when combined with `--fibers`
- Faster processing but less complete target information
- **Example**: `almanac --fibers --no-x-match --mjd-start -7`

#### APOGEE ID -> SDSS ID lookup
Fibers identified only by an old (pre-v1) catalogid or a 2MASS-style
designation are cross-matched through DR17 allStar `apogee_id`. By default
almanac queries only the APOGEE IDs each night needs (in batches, memoised per
process). If the lookup relation named by the config key
`apogee_id_lookup_view` (default `catalogdb.mv_apogee_id_to_sdss_id`) exists,
is readable and is not empty, it is read instead of the four-table join; its
"Last refreshed: YYYY-MM-DD" comment, if any, is logged; a warning is logged only
if a source table was analyzed after that date, and identifiers the view misses are retried with the live join (a match found
that way is logged as a warning that the table is stale). When an identifier
has several sdss_id (several catalogids, each with a rank-1 sdss_id), the
smallest sdss_id is used, on every path.
Each night's HDF5 group records the source used in its
`apogee_id_lookup_*` attributes (`apogee_id_lookup_view_kind` says whether
the relation is a table or a materialized view; the source value stays
`materialized_view` for either). The database maintainers built
`catalogdb.mv_apogee_id_to_sdss_id` on 2026-11-18 as an ordinary table
(sdssdb model `catalogdb.MvApogeeIdToSdssId`, sdssdb main e7d71cb; almanac
reads it with SQL, so no sdssdb upgrade is needed). It is listed in
`almanac pgdump`. The table is a snapshot and must be rebuilt
whenever `sdss_id_flat`, `sdss_id_to_catalog`,
`catalog_to_allstar_dr17_synspec_rev1` or `allstar_dr17_synspec_rev1` is
updated.

#### `--full-apogee-id-lookup`
Build the full lookup (every DR17 allStar entry) once up front instead, and
share it with all worker processes. One heavy query (tens of minutes warm,
hours with a cold database cache) unless the materialized view exists.

#### `--live-apogee-id-join`
Never read the materialized view; always use the live database join.

### Output Control

#### `--output <path>`, `-O <path>`
Write output to HDF5 file at specified path.
- **Incremental**: Appends to existing files, preserves existing data
- **Example**: `almanac --output results.h5 --mjd-start -30`

#### `--skip-existing`
Skip observatory/MJD pairs that already exist in the output file (resume mode).
- Requires `--output`; pairs with an existing `raw/{observatory}/{mjd}/exposures` group are skipped
- An interrupted or partially failed run can simply be re-run with the same command: only the missing pairs are processed
- Existing `/missing_exposures` entries for skipped pairs are preserved
- **Example**: `almanac --mjd-start 59300 --mjd-end 60300 --output big.h5 --skip-existing`

#### `-v`, `--verbosity`
Control output verbosity (stackable).
- No flag: Minimal output
- `-v`: Show progress display
- `-vv`: Show progress display and exposure metadata
- **Example**: `almanac -vv --mjd -1`

### Performance Options

#### `--processes <integer>`, `-p <integer>`
Number of parallel processes for data processing.
- **Default**: Automatic based on available CPU cores
- **Example**: `almanac --processes 4 --mjd-start -30`

### Fault Tolerance

Long queries are fault-isolated per observatory/MJD pair:

- **Retry with backoff**: Transient database connection errors are retried automatically (see the `database_retry_attempts` and `database_retry_backoff` [configuration settings](configuration.md))
- **Per-night isolation**: A night that fails outright is recorded and skipped rather than aborting the whole run; failed pairs are listed at the end
- **Resume**: Re-run the same command with `--skip-existing` to retry only the failed pairs
- **Missing-exposure report**: Every detected missing exposure is recorded in the `/missing_exposures` table of the output file with a reason code (see [Data Formats](data-formats.md))

## Metadata Commands

### `almanac add metadata <file>`
Build a `meta/` group (one row per unique `sdss_id`: catalog cross-match ids, Gaia DR3 astrometry and photometry, 2MASS photometry, SDSS-V targeting flags) from the `sdss_id` in an almanac HDF5 file created with `--fibers`.

**Recommended workflow: never write the almanac file the pipeline reads.** Either decorate a copy, or write a targets-only file:

```bash
cp results.h5 results_meta.h5
almanac add metadata results_meta.h5 --query-workers 4          # decorate the COPY in place
almanac add metadata results.h5 --output targets.h5              # INPUT opened read-only
almanac add metadata new.h5 --output targets_new.h5 --since targets.h5   # incremental
```

**Resumable.** The `sdss_id` are queried in batches (`--batch-size`, default 10,000). Each finished batch is written to `<output or INPUT>.meta-checkpoint/batch_NNNNNN.h5` (temporary name, then rename) and `progress.json` is updated. If the run is killed, or a batch still fails after the per-query reconnect retries, re-running the same command continues from the finished batches; failed batches are re-run after a backoff (`--retry-backoff`, doubling, at most 30 min) up to `--max-batch-attempts` times. A checkpoint made for a different `sdss_id` set or batch size is refused (`--fresh` discards it).

**Atomic final write.** `/meta` is assembled once all batches are done. With `--output` the file is written under a temporary name and renamed; in place, the group is written as `meta__tmp` and renamed to `meta`. A reader never sees a half-written `/meta`. The ids that were queried but not found are kept in `meta_not_found/sdss_id`. Every carton the database returned is kept in `meta_carton_pks/{sdss_id, carton_pk}` (one row per pair), because `sdss5_target_flags` can only encode the cartons the installed `sdss_semaphore` knows (the others are listed in the `unknown_carton_pks` attribute).

**Provenance** (attributes on `/meta`): `almanac_version`, `almanac_commit`, `input_path`, `output_path`, `mode`, `n_sdss_id`, `n_sdss_id_input`, `n_queried`, `n_copied_from_since`, `n_not_found`, `database_host`, `database_name`, `query_start_utc`, `query_end_utc`, `columns`, `since_used`, `since_path`, `restrict_to`, `batch_size`, `query_workers`, `sdss_semaphore_version`, `unknown_carton_pks`, `sdss_id_set_sha256`.

**Options**:
- Date/MJD selection: `--mjd`, `--mjds`, `--mjd-start`/`--mjd-end`, `--date`, `--date-start`/`--date-end` (defaults to every MJD present in the file)
- Observatory selection: `--apo`, `--lco`
- `--query-workers <integer>`: parallel database query workers (default: the `catalog_query_max_workers` configuration setting). Each worker opens its own database connection. `0` runs the queries in-process.
- `--output, -O <file>`: write `/meta` to this new file; INPUT is opened read-only.
- `--since <file>`: copy the `/meta` rows of a previously decorated file or targets file and query only the `sdss_id` it lacks (ids it recorded as not found are not re-queried unless `--retry-not-found`).
- `--restrict-to <file>`: only build rows for these `sdss_id` (`.npy`, `.h5` with `/meta/sdss_id` or `/sdss_id`, or text with one id per line).
- `--checkpoint-dir <dir>`, `--batch-size <n>`, `--fresh`, `--keep-checkpoint`, `--max-batch-attempts <n>`, `--retry-backoff <s>`
- `--extended`: also write 79 value-added columns after the default ones (adapted from upstream sdss/almanac `a922dee`): SDSS_ID position (`ra`, `dec`, `l`, `b`, `healpix` at nside 128, RING), `n_associated`; unWISE W1/W2 fluxes, magnitudes and flags; GLIMPSE 4.5 µm; Bailer-Jones et al. (2021) distances; Gaia DR3 synthetic photometry (GSPC); Zhang, Green & Rix (2023) parameters (`zgr_*`); SDSS-IV APOGEE (DR17) targeting bitmasks (`sdss4_apogee_*`). Off by default: without it `/meta` has exactly the default columns. It runs 8 more queries per batch: measured 2026-11-19 with one query worker, the extra queries cost about 47–77 ms per `sdss_id` on a cold database cache, against about 5–9 ms per `sdss_id` for the default queries on a warm cache, and about 1.4–2.3 ms per `sdss_id` when warm. The checkpoint remembers the mode and is refused in the other one; with `--since`, rows of a file without the extended columns are queried again rather than copied. A value-added query that fails makes the batch fail and be retried (it is never skipped into default values). Upstream's 15 reddening columns (`ebv*`) are not written: nothing fills them. `/meta` gets the attributes `extended` and `extended_column_groups` in this mode only. Needs `healpy` (a declared dependency).
- `--p <integer>`: unused, kept for compatibility.

**Operational note — run this database-local.** `add metadata` throughput is strongly sensitive to database locality and cache state. Measured on a full-survey file (~4.7 million unique `sdss_id`s) with the database on the local network: ~660 ids/s (cold catalog cache; ~2h15m wall), versus 13,000–26,000 ids/s on small warmed-up slices. Over an SSH tunnel the same pass becomes impractically slow. Run `add metadata` on a host local to the SDSS database (e.g. at Utah), never through a tunnel, and budget memory for the final assembly (the finished batches are read back into RAM for the single final write).

## Configuration Commands

### `almanac config show`
Display all current configuration settings and config file location.

```bash
almanac config show
```

**Output includes**:
- All configuration values  
- Configuration file path
- Database connection status

### `almanac config get <key>`
Retrieve specific configuration value.

```bash
almanac config get logging_level
almanac config get sdssdb.host
```

**Nested keys**: Use dot notation for nested configuration values.

### `almanac config set <key> <value>`
Set configuration value persistently.

```bash
almanac config set logging_level 10
almanac config set sdssdb.host custom-host.org
almanac config set database_connect_time_warning 5
```

**Data types**: Values are automatically converted to appropriate types.

## Usage Examples

### Basic Queries

```bash
# Today's observations from both observatories
almanac

# Yesterday's observations with details
almanac --mjd -1 -vv

# Specific observatory
almanac --apo --mjd -1
```

### Date Range Queries  

```bash
# Last week's data
almanac --mjd-start -7 --mjd-end -1

# Specific month
almanac --date-start 2024-01-01 --date-end 2024-01-31

# Single historical date
almanac --date 2021-06-15
```

### Fiber Mapping Analysis

```bash
# Include fiber mappings
almanac --mjd 60000 --fibers

# Fast fiber query (no cross-matching)
almanac --mjd 60000 --fibers --no-x-match

# Get fibers for specific MJD
almanac --mjd 60000 --fibers
```

### Output and Performance

```bash
# Save to file with progress
almanac --output survey.h5 --mjd-start -30 -v

# Parallel processing
almanac --processes 8 --mjd-start -7 --output week.h5

# Verbose output
almanac -vv --mjd-start -7
```

### Configuration Management

```bash
# View all settings
almanac config show

# Set debug logging
almanac config set logging_level 10

# Configure database
almanac config set sdssdb.host your-host.edu
almanac config set sdssdb.port 5432
```

## Advanced Usage Patterns

### Survey Monitoring

```bash
# Daily monitoring script
almanac --mjd -1 --output daily_$(date +%Y%m%d).h5 -v

# Weekly summary with fibers
almanac --mjd-start -7 --fibers --output weekly.h5 -vv
```

### Data Extraction

```bash
# Export specific time period for analysis
almanac --date-start 2024-01-01 --date-end 2024-03-31 \\
        --fibers --output q1_2024.h5 -v

# Export recent observations
almanac --mjd-start -30 --output exposures.h5

# Non-contiguous set of nights
almanac --mjds 58011,59337,60000 --fibers --output testbed.h5
```

### Resumable Bulk Generation

```bash
# Large multi-year run; safe to interrupt and re-run
almanac --mjd-start 59300 --mjd-end 60300 --fibers \
        --output survey.h5 --skip-existing -v

# Then decorate with catalog metadata
almanac add metadata survey.h5 --query-workers 4

# Review what was missing before downstream processing
# (see the /missing_exposures table in survey.h5)
```

### Troubleshooting

```bash
# Debug mode with full verbosity
almanac config set logging_level 10
almanac -vv --mjd -1

# Test database connectivity
almanac config show
almanac --mjd -1 --no-x-match
```

## Exit Codes

- **0**: Success
- **1**: General error (invalid arguments, file errors)  
- **2**: Database connection error
- **3**: Data processing error

## Environment Variables

No special environment variables are required. Configuration is managed through the config file and command-line options.

## Shell Completion

For bash completion support, consider adding alias:

```bash
# Add to ~/.bashrc or ~/.bash_profile  
alias almanac='almanac'
complete -W '--mjd --mjds --mjd-start --mjd-end --date --date-start --date-end --apo --lco --fibers --fibres --no-x-match --full-apogee-id-lookup --live-apogee-id-join --output --skip-existing --processes --verbosity --help' almanac
```

## Tips

1. **Performance**: Use `--no-x-match` for faster queries when SDSS identifiers aren't needed
2. **Storage**: HDF5 files support incremental updates - reuse the same output file
3. **Debugging**: Use `-vv` and set `logging_level` to `10` for maximum detail
4. **Large queries**: Break large date ranges into smaller chunks for better performance
5. **Column selection**: Use custom column lists to reduce output size and processing time