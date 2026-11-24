# API Reference

The modules most useful from Python, with their main entry points. The full,
automatically generated reference (every function and its docstring) is under
[API Documentation](api/modules.rst). The HDF5 layout is described in
[Data Formats](data-formats.md).

## Module Structure

```
almanac/
├── __init__.py          # __version__, config, logger
├── cli.py               # the `almanac` command and its subcommands (click)
├── config.py            # configuration dataclasses and ~/.almanac/config.yaml
├── database.py          # sdssdb connection (connects on import)
├── retry.py             # retry_on_database_error
├── parallel.py          # --processes / --query-workers resolution
├── apogee.py            # the per-night pipeline and the cross-match
├── overrides.py         # exposure overrides and hand-placed targets
├── io.py                # HDF5 writers and readers
├── writer.py            # background HDF5 writer thread
├── signals.py           # clean stops on SIGINT/SIGTERM/SIGHUP
├── exit_codes.py        # ExitStatus: the exit status of each kind of problem
├── metadata.py          # `almanac add metadata`
├── catalog.py           # catalog queries behind /meta
├── qa.py                # known-bad exposure list
├── display.py           # rich display
├── logger.py, utils.py
├── data_models/         # Exposure, FPSTarget, PlateTarget, Source, ExtendedSource, types
└── postprocess/         # helpers of `almanac postprocess`
```

## almanac.config

- `Config`, `DatabaseConfig`, `ObservatoryMJD`: dataclasses with the defaults of every
  setting ([Configuration](configuration.md)).
- `config`: the settings loaded at import from `~/.almanac/config.yaml` (created with the
  defaults if absent); also `almanac.config`.
- `get_config_path() -> Path`: `~/.almanac/config.yaml` (creates `~/.almanac`).
- `ConfigManager.save(config, path)`, `ConfigManager.load(Config, path)`: write / read a
  configuration file (unknown keys are ignored, missing keys take their defaults).

## almanac.database

Importing it connects to the database with the `sdssdb` settings and exposes
`is_database_available` (bool), `database`, and the `catalogdb`, `opsdb` and `targetdb`
model modules of `sdssdb.peewee.sdss5db`. Worker processes import it lazily so that each
opens its own connection.

## almanac.apogee

### `get_almanac_data(observatory, mjd, fibers=False, meta=False, only_assign_sdss_id_to_first_exposure_per_configuration=True, overrides=True)`
Everything `almanac` computes for one night: reads the exposures from the raw headers
(`get_exposures_and_missing`) and runs `process_night`. `meta` turns the cross-match on
(only with `fibers`). Returns `(observatory, mjd, exposures, sequences, missing)`:
the `Exposure` list (exposure `i` at index `i - 1`), `{"objects": [...], "arclamps":
[...], "missing": [...]}` of (first, last) pairs, and the missing-exposure records.

### `process_night(observatory, mjd, exposures, missing=(), fibers=False, meta=False, only_assign_sdss_id_to_first_exposure_per_configuration=True, overrides=True)`
The one per-night pipeline after the exposures are read: exposure overrides, sequences,
and with `fibers` the fiber overrides and `cross_match_targets`. `get_almanac_data` calls
it with exposures from the raw headers; `almanac add fibers` with exposures read back from
a file and rewound by `almanac.overrides.rewind_exposure_overrides`.

### `get_exposures_and_missing(observatory, mjd)`
The night's exposures (gaps filled with `image_type = "missing"`) and the
missing-exposure records (`classify_missing_exposures`; reasons in
`MISSING_EXPOSURE_REASONS`).

### `get_science_sequences(exposures)`, `get_arclamp_sequences(exposures)`, `get_sequences(exposures, image_type, fields, ignore_interrupting_darks=False)`
The sequence definitions ([Data Formats](data-formats.md#sequences)).

### `cross_match_targets(exposures, science_sequences, xmatch=True, only_assign_sdss_id_to_first_exposure_per_configuration=True, include_first_exposure_per_fiber_map_key=False)`
Loads the fiber targets of the first exposure of each object sequence (and, with
`include_first_exposure_per_fiber_map_key`, of the first object exposure of every fiber
table) and, if `xmatch`, sets each target's `sdss_id` in place. Uses the targeted lookups
(`catalogid_sdss_id_lookup`, `gaia_dr2_sdss_id_lookup`, `get_apogee_id_lookup`,
`twomass_sdss_id_lookup`), keeps the best-ranked row and breaks ties to the smallest
`sdss_id` (`best_ranked_lookup`). Unplugged and off-target fibers keep `sdss_id = -1`;
solar-system bodies placed by an override keep the `sdss_id` set there.

### `prepare_cross_match(xmatch, live_apogee_id_join=False, full_apogee_id_lookup=False)`
Once-per-run cross-match setup shared by `almanac --fibers` and `almanac add fibers`:
resolves the APOGEE ID lookup source (`resolve_apogee_id_source`) and, if asked, builds
the full lookup (`create_apogee_id_lookup`). Returns `(source, lookup_attrs,
full_lookup)`; `lookup_attrs` are the `apogee_id_lookup_*` night attributes
(`apogee_id_source_attrs`).

## almanac.overrides

- `load_overrides()`, `load_handplaced_targets()`: parse and validate the packaged files
  (cached); `parse_overrides(text)`, `parse_handplaced_targets(text)` for other text.
- `overrides_for(observatory, mjd)`: the entries of one night.
- `apply_exposure_overrides(exposures, entries)`, `apply_fiber_overrides(exposures,
  entries, targets=None)`: apply them in place (called by `process_night`).
- `rewind_exposure_overrides(exposures)`: return exposures read from a file to their
  header values.
- `override_table_id(mjd, k)`, `is_override_table_id(value)`: the synthetic fiber-table
  IDs (`OVERRIDE_TABLE_ID_BASE = 1_000_000_000`).
- `overrides_attrs(enabled)`: the `exposure_overrides*` and `handplaced_targets_sha256`
  night attributes.
- `OverrideError`.

## almanac.io

### `update(fp, observatory, mjd, exposures, sequences, fibers=False, verbose=False, compression=True)`
Write one night to an open HDF5 file: replaces `raw/<obs>/<mjd>/exposures` and
`sequences`, and with `fibers` calls `write_fibers`. `write_almanac(output, results, ...)`
writes a list of `get_almanac_data` results to a file.

### `open_for_writing(path)`
Context manager: open an almanac file in `"a"` mode for one write session (HDF5
metadata-cache evictions disabled), flushed and closed at the end. The open goes through
`open_h5_retrying(path, mode, budget=None)`, which retries while the HDF5 file lock is
taken (`LOCK_RETRY_BUDGET` 60 s, backoff from 0.1 s to 10 s). The commands that build
files write each night in its own session, so a killed run leaves a readable file.
`read_pending_missing`, `add_pending_missing`, `set_pending_missing` maintain
`/missing_exposures_pending`; `write_run_summary(fp, rows, attrs, replace_keys)` and
`read_run_summary(fp)` the `/run_summary` group.

### `write_fibers(fp, observatory, mjd, exposures, verbose=False)`
Write `raw/<obs>/<mjd>/fibers/<key>`, one group per `Exposure.fiber_map_key`, from the
first object exposure of each key that has targets; existing groups with the same key are
replaced.

### `write_night_attrs(fp, observatory, mjd, override_attrs=None, lookup_attrs=None, clear_stale_lookup_attrs=False)`
The per-night attributes written by both `almanac` and `almanac add fibers`.

### `read_exposures(fp, observatory, mjd)`
Rebuild the `Exposure` models of one night from `raw/<obs>/<mjd>/exposures` (restoring
`original_plate_id`/`original_config_id`), without the raw headers. Empty list if the group
is missing.

### `compare_exposures(group, exposures)`, `compare_sequences(fp, observatory, mjd, sequences)`
Differences between a stored exposures group (or sequences) and what writing the given
models (sequences) would store; empty if identical. Used by `almanac add fibers` to refuse
nights.

### `read_missing_exposures(fp)`, `write_missing_exposures(fp, rows, replace_keys=None)`
The run-level `/missing_exposures` table. With `replace_keys` (a set of (observatory, mjd)),
entries of other nights are kept.

### `get_or_create_group(fp, group_name)`, `delete_hdf5_entry(fp, group_name)`, `write_models_to_hdf5_group(models, group, ...)`
Helpers.

## almanac.metadata

### `build_metadata(input_path, output_path=None, since=None, restrict_to=None, observatories=("apo", "lco"), mjds=None, query_workers=1, batch_size=10000, checkpoint_dir=None, fresh=False, keep_checkpoint=False, retry_not_found=False, max_batch_attempts=8, retry_backoff=60.0, retry_max_delay=1800.0, query_batch=None, ids=None, extended=False)`
What `almanac add metadata` runs. Returns the provenance attributes written on `/meta`.
`query_batch` replaces the catalog query (tests); `ids` replaces the `sdss_id` collected
from the input. Raises `MetadataBuildError` when the build cannot continue (for example no
fiber mappings, or a checkpoint from another build).

## almanac.parallel

- `cpu_budget()`: `(n_cpus, source)` from `SLURM_CPUS_ON_NODE`, the CPU affinity, or
  `os.cpu_count()`.
- `resolve_processes(explicit=None, n_tasks=None)`, `resolve_query_workers(explicit=None,
  config=None, allow_zero=False)`: the `--processes` / `--query-workers` rules, returning
  `(value, source)`.

## almanac.retry

`retry_on_database_error`: decorator (bare or with `attempts`, `backoff`, `max_delay`)
that retries transient database connection errors with reconnect and exponential backoff
(`database_retry_attempts`, `database_retry_backoff`).

## almanac.data_models

- `Exposure`: one exposure. `Exposure.from_path(path)` and `Exposure.from_keys(mjd,
  observatory, exposure, prefix=None, chip=None)` read the raw headers; `targets` reads the
  fiber targets from the `confSummary` or plugmap files on first access.
- `FPSTarget`, `PlateTarget`: fiber rows ([Data Formats](data-formats.md)).
- `source.Source`, `source.ExtendedSource`: `/meta` rows.
- `types`: the `Literal` types (`Category`, `ImageType`, ...) and
  `IDENTIFIED_STAR_CATEGORIES`, `assigns_sdss_id`, `asserts_target_identity`.

## almanac.cli

The click application (`almanac.cli:main`, installed as the `almanac` script). Its
commands are documented in the [CLI Reference](cli-reference.md); `PGDUMP_TABLES` and
`PGDUMP_EXTENDED_TABLES` are the tables `almanac pgdump` dumps.
