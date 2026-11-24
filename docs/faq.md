# Frequently Asked Questions

Common questions and troubleshooting for `almanac`.

## Installation and Setup

### Q: I'm getting "command not found" after installation

**A:** The `almanac` script is installed into the environment's `bin` directory. Run it
through the environment (`uv run almanac` in a checkout synced with `uv sync`), or
activate the environment. To check the installed package:

```bash
uv run python -c "import almanac; print(almanac.__version__, almanac.__file__)"
```

### Q: Which package do I install?

**A:** This fork, from git (see [Installation](installation.md)). The `sdss-almanac`
package on PyPI is upstream's and lacks the fork's changes (exposure overrides,
hand-placed targets, `add fibers`, the resumable `add metadata`, ...). Python 3.11 or newer
is required.

## Database Connection Issues

### Q: "Unable to connect to SDSS database"

**A:**
1. **Check the settings**: `almanac config show` (host, port, user).
2. **Through an SSH tunnel**: `sdssdb.host` must be `localhost` and `sdssdb.port` the local
   end of the tunnel, and the tunnel must be up.
3. **Password**: the PostgreSQL client reads it from `~/.pgpass`, or from the file named by
   `PGPASSFILE` (needed when your home directory is not where the file is). The entry must
   match host, port and user.
4. **Elsewhere**: your address must be allowed to reach the SDSS database.

### Q: The database connection is slow

**A:** almanac warns when connecting takes longer than `database_connect_time_warning`
seconds; raise it to silence the warning (`almanac config set database_connect_time_warning 10`).
For `almanac add metadata`, run on a host local to the database (see the
[User Guide](user-guide.md#adding-catalog-metadata)).

### Q: Can I use almanac without database access?

**A:** Partly. The exposures are read from the raw files, so `almanac --mjd 60000` still
writes the exposures table and sequences, but without the operations database the
missing-exposures report records `db_unavailable`, and without the catalog database fiber
tables have no `sdss_id`. Files for the pipeline must be built with the database.

## Data and File Access

### Q: "No raw data found", or nights come back empty

**A:** almanac reads the raw frames under the `apogee_dir` setting, and the plate,
plugmap and `confSummary` files under `platelist_dir`, `mapper_dir` and `sdsscore_dir`.
The defaults are the Utah paths; elsewhere, point them at your copies
(`almanac config set apogee_dir <path>`). A night with no raw files is reported as having
no data and is not written.

### Q: Why are nights before MJD 59558 not checked against the database?

**A:** The operations database is asked which exposures exist only from
`sdssdb_exposure_min_mjd` (59558 at both observatories). Earlier nights get the
disk-based part of the missing-exposures report only (`hole`).

### Q: Output files are very large

**A:** Fiber tables are the bulk of a file. Leave out `--fibers` when you only need the
exposures (you can add them later with `almanac add fibers`), and split long ranges into
several files.

## Usage and Commands

### Q: What's the difference between --mjd and --date?

**A:**
- **--mjd**: the SDSS MJD of the night (`--mjd 59300`); negative values count back from the
  current MJD (`--mjd -1` is last night).
- **--date**: the same night given as a date (`--date 2021-01-01`).

### Q: What do the verbosity levels do?

**A:**
- **No flag**: log messages only
- **-v**: a live full-screen progress display
- **-vv**: the progress display, then the exposure table of every night

### Q: How do I save data to a file?

**A:**
```bash
almanac --mjd -1 --output results.h5
almanac --mjd -2 --output results.h5  # adds that night to the same file
```

### Q: How do I resume an interrupted run?

**A:** Re-run the same command with `--skip-existing`:

```bash
almanac --mjd-start 59300 --mjd-end 60300 --output big.h5 --fibers --skip-existing
```

Nights already in the output file are skipped, so only missing (or previously failed)
nights are processed. Failed nights are listed at the end of a run and in `/run_summary`,
and the run exits with status 3 (see
[exit status](cli-reference.md#fault-tolerance-and-exit-status)).

## Data Interpretation

### Q: What does "missing exposures" mean?

**A:** An exposure number with no raw file. `almanac` fills it in the exposures table
(`image_type = "missing"`) so that row `i` is always exposure `i`, and, when writing a file,
records it in `/missing_exposures` with a reason code (`hole`, `trailing`, `db_no_file`,
`file_no_db`, or `db_unavailable`) from comparing the files on disk with the exposures the
operations database knows. See [Data Formats](data-formats.md#missing-exposures-report).

### Q: What are "sequences"?

**A:** Three lists of (first, last) exposure-number ranges per night:

- `objects`: runs of consecutive object exposures on the same field, plate and
  configuration. These are the visits: the first exposure of each is the one whose fiber
  targets are cross-matched to `sdss_id`.
- `arclamps`: runs of arc lamp exposures at the same dither position (darks in between
  do not break a run; a run starts and ends with an arc lamp).
- `missing`: runs of missing exposure numbers.

ApogeeReduction.jl does not read them; it uses the exposures table and the fiber tables.
See [Data Formats](data-formats.md#sequences).

### Q: Why does an exposure have a `plate_id` above 1,000,000,000?

**A:** An exposure override edited its fibers (a hand-placed star or Vesta), so it has its
own fiber table under that synthetic ID. The plate it was taken with is in
`original_plate_id`. See [Exposure Overrides](exposure-overrides.md).

### Q: Why is a fiber's category `offtarget`?

**A:** Either its FPS positioner was assigned a target but did not reach it, or an
exposure override says the telescope was pointed away from the plate. Its original
category is in `original_category`, and it has no `sdss_id`.

### Q: What's the difference between plates and FPS?

**A:**
- **Plates (SDSS-IV and earlier)**: fibers plugged into holes drilled in aluminum plates;
  fiber tables keyed by `plate_id`.
- **FPS (SDSS-V)**: robotic fiber positioners reconfigured for each field; fiber tables
  keyed by `config_id`.

## Performance

### Q: almanac is running slowly

**A:**
1. **Use more processes** on a node with spare CPUs (`--processes`), keeping
   `--query-workers` small.
2. **Use the maintainers' APOGEE ID lookup table**: it is used automatically when
   `catalogdb.mv_apogee_id_to_sdss_id` is readable (check the `APOGEE ID lookup:` log line).
3. **Split long ranges** and use `--skip-existing`.

### Q: How many processes should I use?

**A:** Usually the default. Without `--processes`, almanac uses the CPU budget of the job:
`SLURM_CPUS_ON_NODE` inside a Slurm job, otherwise the CPUs the process may run on. The
database load is capped separately by `--query-workers` (default: the
`catalog_query_max_workers` setting, 4). On a shared login node, pass a small
`--processes` explicitly.

```bash
almanac --processes 4 --mjd-start -7
```

## Configuration Issues

### Q: Where is the configuration, and how do I reset it?

**A:** `~/.almanac/config.yaml` (printed by `almanac config show`). Delete it to get the
defaults back; it is recreated on the next run.

### Q: My configuration changes aren't taking effect

**A:**
1. Check the path printed by `almanac config show`.
2. Check the value with `almanac config get <key>`.
3. Check the YAML syntax of the file.

## Error Messages

### Q: `OverrideError`

**A:** The exposure override file, or the hand-placed targets file, is malformed or does
not match the data (for example an entry names an exposure that does not exist). The
message names the entry. See [Exposure Overrides](exposure-overrides.md).

### Q: `add fibers` refuses a night

**A:** The exposures or sequences that the current code and override list produce for that
night differ from the stored ones (the message lists the differences). Rebuild the night
with `almanac`.

### Q: `add metadata` says there are no fiber mappings

**A:** The file was built without `--fibers`. Run `almanac add fibers` (or rebuild with
`--fibers`) first. Nothing was written.

## Getting Help

### Q: How do I report a bug?

**A:** Open an issue on the fork's GitHub repository with:

```bash
uv run python -c "import almanac; print(almanac.__version__)"
python --version
almanac config show         # remove anything private first
almanac --mjd 60000 --apo -vv   # the command that fails, and its log
```
