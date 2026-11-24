# API Reference

This document provides complete API reference for all `almanac` modules.

## Module Structure

```
almanac/
├── __init__.py          # Version information
├── cli.py              # Command-line interface
├── config.py           # Configuration management
├── database.py         # Database connectivity
├── apogee.py           # APOGEE data processing
├── io.py               # HDF5 I/O operations
├── display.py          # Output formatting and display
├── logger.py           # Logging configuration
└── utils.py            # Utility functions
```

## almanac.config

Configuration management for almanac settings.

### Classes

#### `DatabaseConfig`
```python
@dataclass
class DatabaseConfig:
    user: str = "sdss_user"
    host: str = "operations.sdss.org" 
    port: int = 5432
    domain: str = "operations.sdss.*"
```

Database connection configuration.

#### `ObservatoryMJD` 
```python
@dataclass
class ObservatoryMJD:
    apo: int = 59_558  # Apache Point Observatory
    lco: int = 59_558  # Las Campanas Observatory
```

Minimum MJD values for each observatory.

#### `Config`
```python
@dataclass
class Config:
    sdssdb: DatabaseConfig = field(default_factory=DatabaseConfig)
    database_connect_time_warning: int = 3  # seconds
    sdssdb_exposure_min_mjd: ObservatoryMJD = field(default_factory=ObservatoryMJD)
    logging_level: int = 20  # logging.INFO
```

Main configuration class containing all settings.

### Functions

#### `load_config_file(config_path: Optional[str] = None) -> dict`
Load configuration from YAML file.

**Parameters:**
- `config_path`: Path to config file (optional, uses default if None)

**Returns:** Dictionary of configuration values

#### `get_config_path() -> Path`
Get the default configuration file path.

**Returns:** Path object to config file location

## almanac.database

Database connectivity and SDSS database integration.

### Module Variables

#### `is_database_available: bool`
Whether database connection was successfully established.

### Imported Database Models

The module imports and exposes database models from `sdssdb`:
- `catalogdb`: Catalog database models
- `opsdb`: Operations database models

## almanac.apogee

Core APOGEE data processing functionality.

### Constants

#### `RAW_HEADER_KEYS`
```python
RAW_HEADER_KEYS = (
    "DATE-OBS", "FIELDID", "DESIGNID", "CONFIGID", "SEEING",
    "EXPTYPE", "NREAD", "IMAGETYP", "LAMPQRTZ", "LAMPTHAR", 
    "LAMPUNE", "FOCUS", "NAME", "PLATEID", "CARTID", "MAPID",
    "PLATETYP", "OBSCMT", "COLLPIST", "COLPITCH", "DITHPIX",
    "TCAMMID", "TLSDETB"
)
```

FITS header keys extracted from raw APOGEE files.

### Functions

#### `_parse_hexdump_headers(output: List[str], keys: Tuple[str, ...], default: str = "") -> List[str]`
Parse hexdump output to extract header key-value pairs.

**Parameters:**
- `output`: List of strings from hexdump output
- `keys`: Tuple of header keys to look for
- `default`: Default value when key not found

**Returns:** List of extracted values

#### `cross_match_targets(exposures, science_sequences, xmatch=True, only_assign_sdss_id_to_first_exposure_per_configuration=True)`
Load the fiber-to-target mapping for the first exposure of each science sequence and, if `xmatch`, assign each target an `sdss_id` in place. Uses the targeted lookups (APOGEE IDs per night through the maintainers' table unless a full lookup is installed; catalogid, Gaia DR2 and 2MASS helpers), keeps the best-ranked row and breaks ties to the smallest `sdss_id`. Unplugged and off-target fibers keep `sdss_id = -1`. `get_almanac_data(..., fibers=True)` calls it after applying the fiber overrides (with `include_first_exposure_per_fiber_map_key=True` on override nights, so the per-exposure tables are cross-matched too). Solar-system bodies placed by an override keep the `sdss_id` set there.

## almanac.io

HDF5 input/output operations for structured data storage.

### Functions

#### `get_or_create_group(fp, group_name)`
Get existing HDF5 group or create if it doesn't exist.

**Parameters:**
- `fp`: HDF5 file pointer
- `group_name`: Name of the group

**Returns:** HDF5 group object

#### `delete_hdf5_entry(fp, group_name)`
Delete HDF5 group/entry if it exists.

**Parameters:**
- `fp`: HDF5 file pointer  
- `group_name`: Name of entry to delete

#### `write_fibers(fp, observatory, mjd, exposures, verbose=False)`
Write the fiber-to-target mappings for one night to `raw/{observatory}/{mjd}/fibers/{key}`, one group per configuration (or plate); the key is `Exposure.fiber_map_key` (the configuration or plate ID; an exposure override that edits fibers gives its exposure a synthetic ID, so its own table). Only exposures whose `targets` have been populated (see `almanac.apogee.cross_match_targets`) are written; existing groups for the same configuration are replaced.

#### `_update_almanac(fp, exposures, sequence_indices, fiber_maps, compression=True, verbose=False)`
Update HDF5 file with almanac data.

**Parameters:**
- `fp`: HDF5 file pointer
- `exposures`: Exposure data table
- `sequence_indices`: Sequence index arrays
- `fiber_maps`: Fiber mapping data
- `compression`: Enable HDF5 compression
- `verbose`: Enable verbose output

## almanac.cli

Command-line interface implementation using Click.

### Main Command

#### `@click.group(invoke_without_command=True)`
Main CLI entry point with comprehensive options for querying APOGEE data.

**Key Options:**
- `--mjd`: Single MJD query (supports negative relative values)
- `--mjd-start/--mjd-end`: MJD range queries  
- `--date/--date-start/--date-end`: Calendar date queries
- `--apo/--lco`: Observatory selection
- `--fibers/--fibres`: Include fiber mappings
- `--no-x-match`: Skip cross-matching
- `--output/-O`: Output file path
- `--processes/-p`: Parallel processing
- `--verbosity/-v`: Verbosity level (stackable)


### Configuration Subcommands

#### `config show`
Display current configuration settings.

#### `config get <key>`
Get specific configuration value.

#### `config set <key> <value>`
Set configuration value.

## almanac.display

Output formatting and display utilities.

*Note: Specific functions require code examination for detailed documentation.*

## almanac.logger

Logging configuration and setup.

*Note: Specific functions require code examination for detailed documentation.*

## almanac.utils

General utility functions.

*Note: Ensures Yanny table reader/writer registration for astropy.*

## Data Types and Structures

### HDF5 Output Structure

```
observatory/mjd/
├── exposures           # Exposure metadata table
├── sequences          # Nx2 array of exposure ranges forming sequences  
└── fibers/
    ├── fps/configid    # FPS fiber mappings by config ID
    └── plates/plateid  # Plate fiber mappings by plate ID
```

### Exposure Table Columns

Common exposure metadata columns:
- `observatory`: "apo" or "lco" 
- `mjd`: Modified Julian Date
- `exposure`: Exposure number
- `exptype`: Exposure type
- `nread`: Number of reads
- `seeing`: Seeing conditions
- `focus`: Focus position
- `configid`: Configuration ID
- `designid`: Design ID  
- `fieldid`: Field ID

### Fiber Mapping Columns

**FPS (SDSS-V) Columns:**
- `sdss_id`: SDSS identifier
- `catalogid`: Catalog identifier
- `program`: Survey program
- `category`: Target category
- `firstcarton`: Primary carton
- `ra`, `dec`: Target coordinates
- `fiberId`: Fiber identifier

**Plate (SDSS-IV) Columns:**
- `sdss_id`: SDSS identifier
- `target_id`: Target identifier
- `target_ra`, `target_dec`: Target coordinates
- `target_type`: Target classification
- `source_type`: Source classification
- `fiber_id`: Fiber identifier

## Error Handling

The package includes robust error handling for:
- Database connection failures
- Missing raw data files
- Invalid date/MJD specifications
- File I/O errors
- Network connectivity issues

Errors are logged using the configured logging system with appropriate severity levels.

