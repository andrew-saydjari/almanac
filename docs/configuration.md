# Configuration Guide

`almanac` reads its settings from one YAML file. Every key has a default built into the
code (`almanac.config.Config`); the file overrides it. A few command-line options override
a setting for one run (`--query-workers` for `catalog_query_max_workers`, the `--host`,
`--user` and `--port` options of `pgdump`/`pgrestore` for `sdssdb.*`).

## Configuration File

### Location

`~/.almanac/config.yaml` (`$HOME/.almanac/config.yaml`). It is created with the defaults
the first time almanac is imported, if it does not exist. Keys missing from an existing
file take their defaults (they are not written back); keys the code no longer knows are
ignored. An existing file therefore keeps the values written when it was created: a
changed default does not reach it.

### Viewing Configuration

```bash
# Show the configuration file path and every setting
almanac config show

# Get one setting (dot notation for nested settings)
almanac config get logging_level
almanac config get sdssdb.host
```

### Setting Values

```bash
almanac config set logging_level 10
almanac config set sdssdb.host localhost
almanac config set sdssdb.port 63333
```

`config set` converts the value to the type of the key (integer, number, text, or a list
given as YAML or comma-separated, e.g. `almanac config set display_field_names
exposure,image_type`) and refuses a value that does not fit (`logging_level debug` is an
error). A whole section (`sdssdb`, `sdssdb_exposure_min_mjd`) cannot be set at once.
(Values written as text by almanac versions before this change, e.g. `logging_level:
'10'`, still work: almanac converts the numeric settings it reads.)

## Configuration Reference

### Database Settings

#### `sdssdb.user`
- **Type**: String
- **Default**: `"sdss_user"`
- **Description**: Database user. The password is read by the PostgreSQL client from
  `~/.pgpass` (or the file named by `PGPASSFILE`); it is never stored here.

#### `sdssdb.host`
- **Type**: String
- **Default**: `"operations.sdss.org"`
- **Description**: Database host (`localhost` when the database is reached through an
  SSH tunnel).

#### `sdssdb.port`
- **Type**: Integer
- **Default**: `5432`
- **Description**: Database port (the local end of the tunnel, if any).

#### `sdssdb.domain`
- **Type**: String
- **Default**: `"operations.sdss.*"`
- **Description**: Host-name pattern of the sdssdb connection profile almanac creates.

#### `database_connect_time_warning`
- **Type**: Integer
- **Default**: `3`
- **Units**: Seconds
- **Description**: Log a warning if connecting to the database takes longer than this.

#### `database_retry_attempts`
- **Type**: Integer
- **Default**: `5`
- **Description**: Attempts for a query that fails with a transient connection error
  before it is treated as failed (each retry reconnects first).

#### `database_retry_backoff`
- **Type**: Float
- **Default**: `2.0`
- **Units**: Seconds
- **Description**: Delay before the first retry; it doubles with each attempt (at most
  60 s).

#### `catalog_query_max_workers`
- **Type**: Integer
- **Default**: `4`
- **Description**: Default for `--query-workers`, the most database connections almanac's
  worker processes hold at once. In `almanac add metadata` it is the number of query
  worker processes (one connection each). In `almanac` and `almanac add fibers` it is the
  most file-reading workers that hold a connection at once; the other workers keep
  reading files while they wait. Keep it low: the database is shared and often reached
  through a single SSH tunnel.

#### `apogee_id_lookup_view`
- **Type**: String
- **Default**: `"catalogdb.mv_apogee_id_to_sdss_id"`
- **Description**: Relation of (apogee_id, sdss_id) pairs (rank 1) read for the APOGEE ID
  cross-match instead of the live four-table join, when it exists, is readable and is not
  empty. The database maintainers' relation of this name is an ordinary table. Set it to
  an empty string to always use the live join (as `--live-apogee-id-join` does for one
  run). See the [CLI Reference](cli-reference.md#how-targets-are-cross-matched).

#### `sdssdb_exposure_min_mjd.apo`
- **Type**: Integer
- **Default**: `59558`
- **Description**: First MJD for which the operations database is asked which exposures
  were taken at APO (for the missing-exposures report). Earlier nights are not queried.

#### `sdssdb_exposure_min_mjd.lco`
- **Type**: Integer
- **Default**: `59558`
- **Description**: The same for LCO.

### Data Paths

The defaults are the paths on the Utah cluster; elsewhere set them to the local copies.

#### `apogee_dir`
- **Type**: String
- **Default**: `"/uufs/chpc.utah.edu/common/home/sdss/sdsswork/data/apogee/"`
- **Description**: Raw APOGEE frames, as `<apogee_dir>/<obs>/<mjd>/a?R-<chip>-<exposure>.apz`.

#### `sdsscore_dir`
- **Type**: String
- **Default**: `"/uufs/chpc.utah.edu/common/home/sdss50/software/git/sdss/sdsscore/main/"`
- **Description**: The `sdsscore` repository (FPS `confSummary` files under
  `<obs>/summary_files/`).

#### `mapper_dir`
- **Type**: String
- **Default**: `"/uufs/chpc.utah.edu/common/home/sdss50/sdsswork/data/mapper/"`
- **Description**: Plate-era plugmaps (`<obs>/<plugged mjd>/plPlugMapM-*.par`).

#### `platelist_dir`
- **Type**: String
- **Default**: `"/uufs/chpc.utah.edu/common/home/sdss09/software/svn.sdss.org/data/sdss/platelist/trunk/plates/"`
- **Description**: Plate-era `plateHoles` files.

### Display and Logging

#### `display_field_names`
- **Type**: List of strings
- **Default**: `exposure, image_type, n_read, field_id, plate_id, config_id, design_id,
  dithered_pixels, lamp_quartz, lamp_thar, lamp_une, name, observer_comment`
- **Description**: Exposure columns shown by `-vv`.

#### `logging_level`
- **Type**: Integer
- **Default**: `20` (INFO level)
- **Description**: Python logging level
- **Values**:
  - `10`: DEBUG (most verbose)
  - `20`: INFO (default)
  - `30`: WARNING
  - `40`: ERROR
  - `50`: CRITICAL (least verbose)

### Number of processes (`--processes`)
Not a configuration setting. `--processes`/`-p` (`almanac`, `almanac add fibers`,
`almanac postprocess`) defaults to the CPU budget of the job: `SLURM_CPUS_ON_NODE` inside a
Slurm job (or the CPU affinity, if the process is pinned to fewer CPUs), else
`len(os.sched_getaffinity(0))`, else `os.cpu_count()`. An explicit value always wins. The
resolved numbers and where they came from are logged at startup.

## Configuration File Format

```yaml
sdssdb:
  user: sdss_user
  host: operations.sdss.org
  port: 5432
  domain: operations.sdss.*
database_connect_time_warning: 3
database_retry_attempts: 5
database_retry_backoff: 2.0
catalog_query_max_workers: 4
apogee_id_lookup_view: catalogdb.mv_apogee_id_to_sdss_id
sdssdb_exposure_min_mjd:
  apo: 59558
  lco: 59558
logging_level: 20
platelist_dir: /uufs/chpc.utah.edu/common/home/sdss09/software/svn.sdss.org/data/sdss/platelist/trunk/plates/
sdsscore_dir: /uufs/chpc.utah.edu/common/home/sdss50/software/git/sdss/sdsscore/main/
apogee_dir: /uufs/chpc.utah.edu/common/home/sdss/sdsswork/data/apogee/
mapper_dir: /uufs/chpc.utah.edu/common/home/sdss50/sdsswork/data/mapper/
display_field_names:
- exposure
- image_type
# ...
```

## Examples

### Through an SSH tunnel

```bash
almanac config set sdssdb.host localhost
almanac config set sdssdb.port 63333     # the local end of the tunnel
almanac config show
almanac --mjd -1 --apo                   # test with one night
```

### More (or less) logging

```bash
almanac config set logging_level 10      # DEBUG
almanac config set logging_level 30      # WARNING only
```

### Reset to defaults

Delete the file; it is recreated with the defaults on the next run:

```bash
rm ~/.almanac/config.yaml
```

### Several configurations

The file location is fixed, so keep copies and swap them:

```bash
cp ~/.almanac/config.yaml ~/almanac-config-tunnel.yaml
cp ~/almanac-config-local.yaml ~/.almanac/config.yaml
```

## Troubleshooting

**A change does not take effect**: check the path printed by `almanac config show`, the
YAML syntax, and that the key exists (`almanac config get <key>` lists the available keys
when it does not).

**Database connection problems**: check `sdssdb.host`, `sdssdb.port` and `sdssdb.user`,
that the tunnel (if any) is up, and that `~/.pgpass` (or `PGPASSFILE`) has an entry for
that host, port and user. almanac logs "Unable to connect to SDSS database" when the
connection fails; nights are then built without the database (no expected exposures, no
cross-match).
