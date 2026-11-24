# Data Formats

This page describes the HDF5 file `almanac --output` writes, the Pydantic models its
tables are made from, and what downstream code reads. Every group, dataset and attribute
listed here is checked against the writers by the documentation audit script (see
[Development](development.md#documentation-audit)).

## HDF5 Output Structure

```
filename.h5
├── raw/
│   └── {observatory}/                 # apo, lco
│       └── {mjd}/                     # one group per night; attributes: see "Night attributes"
│           ├── exposures/             # one dataset per Exposure column, one row per
│           │                          #   exposure number 1..N
│           ├── sequences/             # objects, arclamps, missing: Nx2 int64
│           │                          #   (first, last) exposure numbers
│           └── fibers/                # with --fibers (or `almanac add fibers`)
│               └── {id}/              # one dataset per fiber column: FPSTarget rows
│                                      #   (key config_id) or PlateTarget rows (key plate_id)
├── missing_exposures/                 # run-level missing-exposures report
├── meta/                              # `almanac add metadata`: one row per sdss_id
├── meta_not_found/                    #   ids queried but not in the catalog
└── meta_carton_pks/                   #   every (sdss_id, carton_pk) pair
```

Every table is a group of equal-length one-dimensional datasets (one per column), each
with a `description` attribute where the model gives one. Strings are fixed-length byte
strings (`|S<n>`); booleans are `bool`; integers `int64`; floats `float64`.

An `almanac add metadata --output` targets file holds only `meta`, `meta_not_found` and
`meta_carton_pks`, and the root attribute `written_by`.

## Exposure Model

`raw/<obs>/<mjd>/exposures` has one row per exposure number from 1 to the larger of the
highest number on disk and the highest number the operations database knows; row `i` is
exposure `i`. Numbers with no file get a row with `image_type = "missing"` and default
values elsewhere. Columns come from `almanac.data_models.Exposure`; aliases are the raw
header keywords.

### Basic Information

| Field | Type | Description |
|-------|------|-------------|
| `observatory` | `Observatory` | Observatory name ("apo" or "lco") |
| `mjd` | `int` | MJD of the night |
| `exposure` | `int` | Exposure number within the night (≥ 1) |
| `prefix` | `Optional[Prefix]` | Raw file basename prefix ("apR" at APO, "asR" at LCO) |
| `file_size` | `int` | Size of the raw file in bytes (-1 for missing exposures) |

### Exposure Metadata

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `name` | `str` | | Header NAME (in the plate era the plugged plate name `<plate>-<plugged mjd>-<iteration>`, e.g. `5099-55753-01`; `0000-00000-00` in the FPS era) |
| `n_read` | `int` | `nread` | Number of detector reads (≥ 0) |
| `image_type` | `ImageType` | `imagetyp` | Image type, lower case; `twilightflat` also when the observer comment mentions a sky or twilight flat |
| `observer_comment` | `str` | `obscmnt` | Observer comment (stripped; "None" becomes empty) |

### Survey Identifiers

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `map_id` | `int` | `mapid` | Map identifier |
| `cart_id` | `int` | `cartid` | Cartridge ID (0 for "FPS") |
| `plate_id` | `int` | `plateid` | Plate ID (plate era); a synthetic ID ≥ 1,000,000,000 when an exposure override gave the exposure its own fiber table |
| `field_id` | `int` | `fieldid` | Field identifier |
| `design_id` | `int` | `designid` | Design identifier |
| `config_id` | `int` | `configid` | Configuration ID (FPS era); changed by a `use_config` override, synthetic as for `plate_id` |

Missing identifiers are -1.

### Observing Conditions

| Field | Type | Description |
|-------|------|-------------|
| `seeing` | `float` | Seeing in arcseconds |

### Instrument State

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `focus` | `float` | | Focus position |
| `collpist` | `float` | | Collimator piston |
| `colpitch` | `float` | | Collimator pitch |
| `dithered_pixels` | `float` | `dithpix` | Dither position in pixels |
| `lamp_quartz` | `int` | `lampqrtz` | Quartz lamp state (-1 unknown, 0 off, 1 on) |
| `lamp_thar` | `int` | `lampthar` | ThAr lamp state (-1, 0, 1) |
| `lamp_une` | `int` | `lampune` | UNe lamp state (-1, 0, 1) |

### Exposure Overrides

| Field | Type | Description |
|-------|------|-------------|
| `override_ids` | `str` | `;`-separated ids of the exposure overrides applied to this exposure (empty if none) |
| `override_actions` | `str` | `;`-separated description of what each did, e.g. `not_science`, `use_config(B->A)`, `retarget_fibers(203=VESTA;others=offtarget)` |

### Computed Properties

| Property | Type | Description |
|----------|------|-------------|
| `exposure_string` | `str` | Eight-digit exposure number used in raw file names: `max(0, (mjd - 55562) * 10000) + exposure` |
| `fps` | `bool` | FPS era: MJD ≥ 59423 at APO, ≥ 59809 at LCO |
| `flagged_bad` | `bool` | Listed in `src/almanac/etc/bad_exposures.csv`, or an object exposure with no plate (plate era) or configuration (FPS era) ID, or marked `not_science` by an exposure override |
| `chip_flags` | `int` | Which raw chip files exist: bit 0 chip a, bit 1 chip b, bit 2 chip c (7 = all three) |
| `fiber_map_key` | `str` | Name of this exposure's table under `fibers/`: always `str(config_id)` (FPS) or `str(plate_id)` (plate era) |
| `original_plate_id` | `int` | Plate ID from the raw header; differs from `plate_id` only when an exposure override gave the exposure its own fiber table |
| `original_config_id` | `int` | Configuration ID from the raw header; differs from `config_id` only when an override changed the configuration or gave the exposure its own fiber table |

### Type Definitions

**`Observatory`**: `Literal["apo", "lco"]`

**`Prefix`**: `Literal["apR", "asR"]`

**`ImageType`**: `Literal["blackbody", "dark", "object", "domeflat", "arclamp", "twilightflat", "internalflat", "quartzflat", "missing"]`

## Sequences

`raw/<obs>/<mjd>/sequences` holds three datasets, always present (shape `(0, 2)` when
empty), each an `N x 2` int64 array of inclusive (first, last) exposure numbers, in
exposure order:

<!-- audit:table sequences -->
| Dataset | Definition |
|---------|------------|
| `objects` | Runs of consecutive `object` exposures with the same `field_id`, `plate_id` and `config_id` (after the exposure overrides, so an exposure with its own synthetic fiber-table ID is a run of its own) |
| `arclamps` | Runs of `arclamp` exposures with the same `dithered_pixels`, where `dark` exposures (same dither) between two arc lamps do not interrupt a run. A range starts and ends with an arc lamp; darks alone are not an entry |
| `missing` | Runs of exposure numbers with `image_type = "missing"` |

The object sequences are the visits of a night. They decide which exposures' fiber
targets are cross-matched: the first exposure of each object sequence (and, on nights
with exposure overrides, the first object exposure of every fiber table). The fiber table
of a plate or configuration is written from the first object exposure that used it, which
always starts an object sequence. `almanac add fibers` recomputes the sequences and
refuses a night where they differ from the stored ones. `almanac dump visits` is not
implemented, and `almanac dump stars` counts nights, not sequences.

## Fiber Tables

`raw/<obs>/<mjd>/fibers/<id>` holds one table per plate or FPS configuration observed
that night (`--fibers`). `<id>` is the `fiber_map_key` of the exposures that used it:
their `config_id` (FPS era) or `plate_id` (plate era). To find the fibers of an exposure,
open `fibers/<config_id>` or `fibers/<plate_id>` of its row in the exposures table.

FPS tables have one row per APOGEE fiber, 300 rows sorted by `fiber_id`: fibers that the
`confSummary` file does not list (the FPI fibers, which have no positioner) get a stub row
with `category = "bonus"` and NaN coordinates. The `confSummary` flavor is the first that
exists of `confSummaryFS`, `confSummaryS` and `confSummary`. Plate tables have one row per
plugged fiber matched to its planned hole, sorted by `fiber_id`.

## FPSTarget Model

`almanac.data_models.FPSTarget`: SDSS-V Fiber Positioning System fibers. Aliases are the
`confSummary` column names.

### Target Identification

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `sdss_id` | `Int64` | | SDSS identifier from the cross-match (-1 if none) |
| `catalogid` | `Int64` | | Catalog identifier |
| `twomass_designation` | `str` | `tmass_id` | 2MASS designation |
| `category` | `Category` | | Target category (see "Off-target fibers", below) |
| `original_category` | `Category` | | Category reported by the configuration summary file, kept only when `almanac` has changed `category` |
| `cadence` | `str` | | Observing cadence |
| `firstcarton` | `str` | | Primary carton name |
| `program` | `str` | | Survey program |

### Positioner Information

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `positioner_id` | `int` | `positionerId` | Positioner ID |
| `hole_id` | `str` | `holeId` | Hole identifier |
| `hole_type` | `HoleType` | `holeType` | Hole type |
| `planned_hole_type` | `HoleType` | `holetype` | Planned hole type |
| `fiber_type` | `str` | `fiberType` | Fiber type |
| `assigned` | `bool` | | Target assigned to fiber |

### Status Flags

| Field | Type | Description |
|-------|------|-------------|
| `on_target` | `bool` | Fiber positioned on target |
| `disabled` | `bool` | Fiber is disabled |
| `valid` | `bool` | Valid coordinate conversion |
| `decollided` | `bool` | Positioner was decollided |

### Coordinates

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `ra` | `float` | `racat` | Right Ascension (degrees) |
| `dec` | `float` | `deccat` | Declination (degrees) |
| `alt` | `float` | `alt_observed` | Observed altitude (degrees) |
| `az` | `float` | `az_observed` | Observed azimuth (degrees) |

### Position Coordinates

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `x_wok` | `float` | `xwok` | X coordinate in wok frame |
| `y_wok` | `float` | `ywok` | Y coordinate in wok frame |
| `z_wok` | `float` | `zwok` | Z coordinate in wok frame |
| `x_focal` | `float` | `xFocal` | X coordinate in focal plane |
| `y_focal` | `float` | `yFocal` | Y coordinate in focal plane |

### Positioner Angles

| Field | Type | Description |
|-------|------|-------------|
| `alpha` | `float` | Alpha angle of positioner arm |
| `beta` | `float` | Beta angle of positioner arm |

### Wavelength Information

| Field | Type | Description |
|-------|------|-------------|
| `lambda_design` | `float` | Design wavelength |
| `lambda_eff` | `float` | Effective wavelength |
| `coord_epoch` | `float` | Coordinate epoch |

### Instrument Identifiers

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `spectrograph_id` | `int` | `spectrographId` | Spectrograph ID |
| `fiber_id` | `int` | `fiberId` | Fiber ID |

### Position Offsets

| Field | Type | Description |
|-------|------|-------------|
| `delta_ra` | `float` | RA offset applied to fiber |
| `delta_dec` | `float` | Dec offset applied to fiber |

### Target of Opportunity

| Field | Type | Description |
|-------|------|-------------|
| `too` | `bool` | Target of opportunity flag |
| `too_id` | `int` | TOO identifier |
| `too_program` | `str` | TOO program name |

### Exposure Override Columns

| Field | Type | Description |
|-------|------|-------------|
| `override_id` | `str` | Id of the exposure override that changed this fiber (empty if none) |
| `override_target` | `str` | Name of the object an override says was in this fiber (a target of `handplaced_targets.yaml`; empty if none) |
| `override_kind` | `str` | `star` (cross-matched as usual) or `solar_system` (not cross-matched; `sdss_id` set by the override); empty if none |

## PlateTarget Model

`almanac.data_models.PlateTarget`: SDSS-IV plate fibers, from the plugmap
(`plPlugMapM`) matched to the planned holes (`plateHoles`).

### Target Identification

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `sdss_id` | `Int64` | | SDSS identifier from the cross-match (-1 if none) |
| `catalogid` | `Int64` | | Catalog identifier |
| `twomass_designation` | `str` | | 2MASS designation computed from `twomass_id` (computed property) |
| `twomass_id` | `str` | `tmass_id` | 2MASS ID |
| `target_ids` | `str` | `targetids` | Target ID string |
| `category` | `Category` | `targettype` | Target category (plate `sky` becomes `sky_apogee`, `standard` becomes `standard_apogee`, `na` becomes empty) |
| `original_category` | `Category` | | Plugmap category, kept only when `almanac` has changed `category` (exposure overrides) |

### Survey Information

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `observatory` | `Observatory` | | Observatory (used by the fiber mapping fixes) |
| `hole_type` | `HoleType` | `holeType` | Hole type |
| `planned_hole_type` | `HoleType` | `holetype` | Planned hole type |
| `obj_type` | `ObjType` | `objType` | Object type |
| `assigned` | `bool` | | Assignment flag |

### Status Flags

| Field | Type | Description |
|-------|------|-------------|
| `conflicted` | `bool` | Fiber conflict flag |
| `ranout` | `bool` | Ran out of targets flag |
| `outside` | `bool` | Outside survey area flag |

### Coordinates

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `ra` | `float` | | Right Ascension (degrees) |
| `dec` | `float` | | Declination (degrees) |
| `x_focal` | `float` | `xfocal` | X focal plane coordinate |
| `y_focal` | `float` | `yfocal` | Y focal plane coordinate |
| `xf_default` | `float` | | Default X focal coordinate |
| `yf_default` | `float` | | Default Y focal coordinate |

### Wavelength Information

| Field | Type | Description |
|-------|------|-------------|
| `lambda_eff` | `float` | Effective wavelength |
| `zoffset` | `float` | Z offset |

### Instrument Identifiers

| Field | Type | Alias | Description |
|-------|------|-------|-------------|
| `spectrograph_id` | `int` | `spectrographId` | Spectrograph ID |
| `fiber_id` | `int` | `fiberId` | Fiber ID |
| `planned_fiber_id` | `int` | `fiberid` | Planned fiber ID |
| `throughput` | `int` | | Throughput value |

### Plate-Specific Fields

| Field | Type | Description |
|-------|------|-------------|
| `iplateinput` | `int` | Plate input ID |
| `pointing` | `int` | Pointing number |
| `offset` | `int` | Offset value |
| `block` | `int` | Block number |
| `iguide` | `int` | Guide flag |
| `bluefiber` | `int` | Blue fiber flag |
| `chunk` | `int` | Chunk number |
| `ifinal` | `int` | Final flag |
| `plugged_mjd` | `int` | MJD when plate was plugged |
| `fix_fiber_flag` | `int` | Fiber mapping fix indicator |

### Physical Properties

| Field | Type | Description |
|-------|------|-------------|
| `diameter` | `float` | Target diameter |
| `buffer` | `float` | Buffer size |
| `priority` | `int` | Target priority |

### Exposure Override Columns

| Field | Type | Description |
|-------|------|-------------|
| `override_id` | `str` | Id of the exposure override that changed this fiber (empty if none) |
| `override_target` | `str` | Name of the hand-placed object in this fiber (empty if none) |
| `override_kind` | `str` | `star` or `solar_system` (empty if none) |

## Type Definitions

### Category Types

**`Category`**: `Literal["", "bonus", "science", "veto_location_boss", "veto_location_apogee", "sky_apogee", "sky_boss", "standard_apogee", "standard_boss", "open_fiber", "unplugged", "offtarget", "retargeted"]`

`veto_location_boss` and `veto_location_apogee` appear in manually built FPS
designs. They are APOGEE fibers placed on real catalog stars: Gaia bright-neighbor
stars (`veto_location_boss`, carton
`ops_gaia_brightneighbors`) and bright 2MASS stars (`veto_location_apogee`,
carton `ops_2mass_psc_brightneighbors`). `almanac` keeps these category names
unchanged, cross-matches the fibers to an `sdss_id` exactly as it does `science`
fibers, and includes them in star-level summaries. Like `science` fibers, they
assert a target identity, so an assigned-but-off-target one is relabeled
`offtarget` (see "Off-target fibers", below). The full list of categories
treated as identified stars is `almanac.data_models.types.IDENTIFIED_STAR_CATEGORIES`.

`retargeted` is no longer written: hand-placed objects are `science` with
`override_target`/`override_kind` set. It is still accepted so that older files and
tests validate. Fibers with category `unplugged`, `offtarget` or `retargeted` are never
cross-matched.

### Hole Types

**`HoleType`**: `Literal["object", "coherent_sky", "guide", "light_trap", "alignment", "quality", "manga", "manga_single", "manga_alignment", "acquisition_center", "acquisition_offaxis", "apogee", "center", "trap", "boss", "apogee_shared", "apogee_south", "bosshalf", "boss_shared", "fps", "unplugged"]`

### Object Types

**`ObjType`**: `Literal["galaxy", "qso", "star_bhb", "star_carbon", "star_brown_dwarf", "star_sub_dwarf", "star_caty_var", "star_red_dwarf", "star_white_dwarf", "redden_std", "spectrophoto_std", "hot_std", "rosat_a", "rosat_b", "rosat_c", "rosat_d", "serendipity_blue", "serendipity_first", "serendipity_red", "serendipity_distant", "serendipity_manual", "qa", "sky", "na"]`

## Data Validation and Processing

### Automatic Data Cleaning

The Pydantic models include automatic validation and cleaning:

- **Empty strings to integers**: empty identifiers become -1
- **Lamp states**: 'F'/'T' (or '0'/'1') become 0/1; anything else -1
- **Float validation**: values that cannot be converted become NaN
- **Case normalization**: image types, hole types and object types are lower case
- **Comment sanitization**: observer comments are stripped, "None" becomes empty

### Model Validators

#### Exposure Model
- Sets the prefix from the observatory when it is not given
- Sanitizes observer comments
- Converts lamp states
- Relabels sky and twilight flats as `twilightflat` from the observer comment

#### FPSTarget Model
- Fixes early fiber mapping duplicates for spectrograph 2
- Standardizes 2MASS designations
- Relabels off-target fibers (see below)

### Off-target fibers

An FPS positioner can be assigned a target (`assigned = 1`) and yet not reach it
(`on_target = 0`), because it was moved to resolve a mechanical collision
(`decollided = 1`), because the fiber is disabled, or because the on-sky
coordinates could not be converted to robot angles. The fiber still collects
light, but from some other patch of sky: on apo MJD 60774 one such fiber
observed a field star 9 arcmin from its assigned target.

The configuration summary file keeps reporting the assigned target's category and
identifiers for these fibers. `almanac` therefore relabels them:

| Field | Off-target fiber |
|-------|------------------|
| `category` | `offtarget` |
| `original_category` | the category the configuration summary file reported (e.g. `science`) |
| `sdss_id` | `-1` (never cross-matched, as for unplugged fibers) |

The predicate is `assigned == 1 and on_target == 0`, and it is applied only to
categories that assert a target identity: sky, unplugged, and empty categories
are left alone. Everything else the configuration summary file reported —
`assigned`, `on_target`, `valid`, `disabled`, `decollided`, `catalogid`, `ra`,
`dec`, `alpha`, `beta`, the wok and focal-plane coordinates — is written
unchanged, so these fibers stay identifiable and the relabeling is reversible.

The plate era has no equivalent columns, so `PlateTarget` rows are never
relabeled this way (an exposure override can still mark plate fibers `offtarget`, see
[Exposure Overrides](exposure-overrides.md)).

#### PlateTarget Model
- Applies historical fiber mapping fixes for specific plugged-MJD ranges at APO
  (`fix_fiber_flag` records it)
- Translates plate-era categories to FPS-era equivalents

## Synthetic fiber-table IDs

An exposure whose fibers an exposure override edits (`retarget_fibers`) gets its own fiber
table under a synthetic ID, `1_000_000_000 + (mjd - 50000) * 1000 + k` (`k` = 1, 2, ... per
night), that **replaces its `plate_id`** (FPS: `config_id`) in the exposures table. The
header value is kept in `original_plate_id` (`original_config_id`). Anyone joining on
`plate_id` or `config_id` should therefore:

- treat IDs ≥ 1,000,000,000 as almanac fiber tables, not plates or configurations
  (`almanac.overrides.is_override_table_id`);
- join on `original_plate_id`/`original_config_id` to get the plate or configuration the
  exposure was taken with;
- note that each such exposure is an object sequence of its own, because the ID differs
  from the plate's.

## Night attributes

Each `raw/<obs>/<mjd>` group records how the night was built (written by both `almanac`
and `almanac add fibers`):

<!-- audit:table night_attrs -->
| Attribute | Meaning |
|-----------|---------|
| `exposure_overrides` | `enabled` or `disabled` (`--no-overrides`) |
| `exposure_overrides_sha256` | SHA-256 of the packaged `exposure_overrides.yaml` (only when enabled) |
| `handplaced_targets_sha256` | SHA-256 of the packaged `handplaced_targets.yaml` (only when enabled) |
| `apogee_id_lookup_source` | `materialized_view` (the configured lookup relation was read; the value is the same when that relation is a table) or `live_join` |
| `apogee_id_lookup_mode` | `targeted` (per-night queries) or `full` (`--full-apogee-id-lookup`) |
| `apogee_id_lookup_view` | Name of the relation read (empty with the live join) |
| `apogee_id_lookup_view_kind` | What that relation is: `table`, `materialized view`, ... (empty with the live join) |
| `apogee_id_lookup_view_last_refreshed` | Date from its `Last refreshed: YYYY-MM-DD` comment (empty if none) |
| `apogee_id_lookup_view_comment` | Its comment |
| `apogee_id_lookup_staleness_warnings` | `; `-separated staleness warnings (empty if none) |
| `apogee_id_lookup_note` | Why the live join was used (empty when the relation was read) |

The `apogee_id_lookup_*` attributes are written only when targets were cross-matched
(`--fibers` without `--no-x-match`). Files built before these features lack the
attributes.

## Missing-Exposures Report

The run-level `/missing_exposures` group records every exposure that was expected but not
found, comparing the exposures on disk against the exposure numbers known to the
operations database (queried from MJD 59558, `sdssdb_exposure_min_mjd`). It contains
parallel datasets:

<!-- audit:table missing_exposures -->
| Dataset | Type | Description |
|---------|------|-------------|
| `observatory` | string | Observatory ("apo" or "lco") |
| `mjd` | integer | MJD of the night |
| `exposure` | integer | Missing exposure number (`-1` for the `db_unavailable` sentinel) |
| `expected_max_db` | integer | Highest exposure number known to the database for that night (-1 if unknown) |
| `reason` | string | Reason code (see below) |

**Reason codes**:

| Reason | Description |
|--------|-------------|
| `hole` | No file on disk and no database record; interior gap in the on-disk exposure numbers |
| `trailing` | No file on disk and no database record; beyond the last on-disk exposure but at or below the database maximum |
| `db_no_file` | The operations database has a record of this exposure, but no file exists on disk |
| `file_no_db` | A file exists on disk, but the operations database has no record of it |
| `db_unavailable` | Sentinel (`exposure = -1`) recording that the database could not be reached for this night, so trailing missing exposures are undetectable |

Entries are refreshed per observatory/MJD pair on each run; pairs not processed in a run
(skipped with `--skip-existing`, or failed) keep their previous entries. The table is
written at the end of a run (also when it is stopped by a signal). After a run killed
outright (`kill -9`), the nights it wrote whose rows were not recorded are listed in the
`/missing_exposures_pending` dataset (strings `"obs/mjd"`); the next run with
`--skip-existing` processes them again and removes the dataset. It is absent from a file
whose last run ended normally. Exposures missing
on disk also appear in `raw/{observatory}/{mjd}/exposures` with `image_type="missing"` so
that exposure numbering stays dense.

## Run summary

Every run of `almanac --output` writes the `/run_summary` group: a table of the nights that
had a problem, accumulated over runs into the same file (rows of an earlier run are kept for
nights this run did not process, e.g. skipped with `--skip-existing`; a night retried
successfully is removed), with the counts of the last run as attributes.

<!-- audit:table run_summary -->
| Dataset | Type | Description |
|---------|------|-------------|
| `observatory` | string | Observatory ("apo" or "lco") |
| `mjd` | integer | MJD of the night |
| `category` | string | `exception`, `database_error`, `write_failed`, `pool_broken` (the night is not in the file) or `db_unavailable` (written without the operations database) |
| `message` | string | The error message (empty for `db_unavailable`) |

<!-- audit:table run_summary_attrs -->
| Attribute | Description |
|-----------|-------------|
| `last_run_n_requested` | Observatory/MJD pairs requested |
| `last_run_n_skipped_existing` | Skipped by `--skip-existing` |
| `last_run_n_with_data` | Processed, with exposures (written) |
| `last_run_n_no_data` | Processed, no exposure files on disk (nothing written) |
| `last_run_n_failed` | Failed (not written) |
| `last_run_n_db_unavailable` | Written without the operations database |
| `last_run_n_not_processed` | Not reached because the run was stopped by a signal |
| `last_run_exit_status` | Exit status of the run (see [exit status](cli-reference.md#fault-tolerance-and-exit-status)) |
| `last_run_finished_utc` | When the run finished (UTC, ISO 8601) |
| `last_run_almanac_version` | almanac version |
| `last_run_cross_match` | Whether the cross-match was requested (`--fibers` without `--no-x-match`) |

## Catalog metadata (`/meta`)

`almanac add metadata` writes `/meta`: one row per `sdss_id`, sorted, with the columns of
`almanac.data_models.source.Source` (identifiers of the catalogs the `sdss_id` is matched
to, Gaia DR3 astrometry, photometry and radial velocities, 2MASS photometry, target of
opportunity, and the `sdss5_target_flags` bitmask), plus the 79 columns of
`ExtendedSource` (groups in the `extended_column_groups` attribute) with `--extended`.
Each column has a `description` attribute. A hand-placed solar-system body has a fixed
row: `sdss_id = 0`, `lead = "solar_system"`, every catalog column at its default.

Side groups:

<!-- audit:table meta_side_groups -->
| Dataset | Content |
|---------|---------|
| `meta_not_found/sdss_id` | Ids that were queried but have no catalog row |
| `meta_carton_pks/sdss_id` | One row per (sdss_id, carton) pair the database returned... |
| `meta_carton_pks/carton_pk` | ...and the carton's primary key (cartons `sdss_semaphore` does not know included) |

Attributes of `/meta` (provenance):

<!-- audit:table meta_attrs -->
| Attribute | Meaning |
|-----------|---------|
| `almanac_version` | Package version |
| `almanac_commit` | Git commit of the almanac checkout (`+dirty` if modified; `UNKNOWN` outside git) |
| `database_host` | `host:port` from the configuration (no credentials) |
| `database_name` | `sdss5db` |
| `input_path` | Absolute path of INPUT |
| `output_path` | File written (INPUT when in place) |
| `mode` | `output` or `in_place` |
| `n_sdss_id` | Rows in `/meta` |
| `n_solar_system_rows` | Fixed solar-system rows (0 or 1) |
| `n_sdss_id_input` | Unique positive `sdss_id` in INPUT |
| `n_sdss_id_requested` | After `--restrict-to` |
| `n_queried` | Ids queried in this build |
| `n_copied_from_since` | Rows copied from `--since` |
| `n_not_found` | Rows of `meta_not_found` |
| `since_used` | Whether `--since` was given |
| `since_path` | Its path (empty if not) |
| `restrict_to` | `--restrict-to` path (empty if not) |
| `query_start_utc` | When the first batch started |
| `query_end_utc` | When the last batch ended |
| `written_utc` | When `/meta` was written |
| `batch_size` | `--batch-size` |
| `query_workers` | `--query-workers` (resolved) |
| `sdss_semaphore_version` | Version of `sdss-semaphore` used to encode `sdss5_target_flags` |
| `unknown_carton_pks` | Cartons `sdss_semaphore` does not know (int64 array) |
| `n_unknown_carton_pks` | Their number |
| `n_carton_pairs` | Rows of `meta_carton_pks` |
| `n_copied_without_raw_carton_pks` | Rows copied from a `--since` file that had no `meta_carton_pks` |
| `columns` | Column names in order |
| `sdss_id_set_sha256` | SHA-256 of the requested `sdss_id` set |
| `extended` | `True` (only with `--extended`) |
| `extended_column_groups` | Names of the extended column groups (only with `--extended`) |

## What downstream code reads

ApogeeReduction.jl (`src/ar1D.jl`, `src/utils.jl`) reads, for each night,
`raw/<obs>/<mjd>/exposures` (as a table; row `i` is exposure `i`) and, for an object
exposure, `raw/<obs>/<mjd>/fibers/<config_id>` (FPS era) or `fibers/<plate_id>` (plate
era) using the value in that exposure's row; it relies on the FPS `bonus` stub rows for
the FPI fibers and on the `category`, `fiber_type`, `fiber_id` and `sdss_id` columns. It
does not read `sequences`, the night attributes, `/missing_exposures` or `/meta`. Because
the synthetic fiber-table IDs replace `plate_id`/`config_id`, it opens the edited table of
an overridden exposure with no change.

## Data Access

```python
import h5py
import numpy as np

with h5py.File("survey.h5", "r") as fp:
    night = fp["raw/apo/60000"]
    exposures = {k: v[:] for k, v in night["exposures"].items()}
    i = 13 - 1                                    # exposure 13
    key = exposures["fiber_map_key"][i].decode()  # str(config_id) or str(plate_id)
    fibers = {k: v[:] for k, v in night[f"fibers/{key}"].items()}
    objects = night["sequences/objects"][:]       # (first, last) exposure numbers
```

The models can also be built directly from raw files:

```python
from almanac.data_models import Exposure, FPSTarget, PlateTarget

exposure = Exposure.from_path("<apogee_dir>/apo/60000/apR-a-44380013.apz")
print(exposure.fps, exposure.exposure_string, exposure.flagged_bad)

# Targets are read from the confSummary / plugmap files on first access
for target in exposure.targets:
    print(target.fiber_id, target.category, target.sdss_id)
```

## Data Quality and Validation

### Missing Value Handling

- **Numeric fields**: NaN for floats, -1 for identifiers and integer flags
- **String fields**: empty strings
- **Boolean fields**: False

### Quality Assurance

- **Bad exposure flagging**: each exposure carries the computed `flagged_bad` column (see
  above). The `almanac flag` subcommand is an unimplemented stub and should not be used.
- **Exposure overrides**: see [Exposure Overrides](exposure-overrides.md).
- **Fiber mapping corrections**: historical fixes for known fiber mapping errors.
