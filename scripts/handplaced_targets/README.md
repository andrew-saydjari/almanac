# Hand-placed objects (2011 APOGEE commissioning)

During commissioning in 2011 the observers repeatedly pointed the 2.5 m
telescope so that a bright star (once the asteroid Vesta) fell on one chosen
fiber of whatever plate was plugged. The plugmap still gives every fiber its
plate target, so without an override the star's spectrum is labelled as an
unrelated plate star, and every other fiber (which saw random sky) keeps its
plate target too.

Two files describe these exposures:

| file | what | made by |
|---|---|---|
| `src/almanac/etc/handplaced_targets.yaml` | targeting information per object name: 2MASS designation (or SDSS-V `catalogid` if there is none), ICRS J2000 ra/dec, provenance; `kind: solar_system` for Vesta | `resolve_targets.py --db --write` (SIMBAD, 2MASS PSC via VizieR, catalogdb for the catalogid fallback) |
| block `handplaced-2011` of `src/almanac/etc/exposure_overrides.yaml` | the `retarget_fibers` / `not_science` entries | `make_overrides.py --write-yaml` from `handplaced_2011.csv` |

The shipped APO 55755 entries (Vesta and the five stars of the "AADAF" test)
are written by hand above the block and use the same targets file.

## How almanac uses them

`almanac.apogee.get_almanac_data` applies `retarget_fibers` **before** the
cross-match: each affected exposure gets a copy of its plate's fiber table in
which the named fiber's row is rewritten from the targets file (category
`science`, the star's designation, ra, dec; `original_category`,
`override_id`, `override_target` record the change) and the other fibers become
`offtarget`. The ordinary cross-match then runs on that copy, so the star gets
its `sdss_id` through the same lookups as any plugged fiber (DR17 allStar
APOGEE ID lookup, then 2MASS PSC; or the catalogid lookup), and
`almanac add metadata` picks the `sdss_id` up from the edited table like any
other. The edited table is stored under a synthetic ID that replaces the
exposure's `plate_id` (header value kept in `original_plate_id`), so
ApogeeReduction.jl, which opens `fibers/<plate_id>`, reads it with no change. No `sdss_id` is stored in either file.

A solar-system body has no fixed position and no catalogue entry: its row
keeps category `retargeted`, ra/dec NaN, no designation, and `sdss_id` =
`solar_system_sdss_id` from the targets file (0; see the note there: this
is a setting, not a verified SDSS convention). Its position at each exposure
is the raw-header RA/DEC of that exposure (or JPL Horizons, designation 4).

## The inventory: `handplaced_2011.csv`

One row per exposure of the 2011 tests outside APO 55755: night, exposure,
plate, fiber, object, `action`, where the fiber number came from
(`log` / `log-inherit` / `header`), the fiber-level data check, the
separation of the raw-header pointing from the star and from the plate's
targets, the verbatim log line(s) and a note.

The rule:

* `retarget_fibers`: the data check finds the stated fiber as the brightest
  trace (more than 10 MAD above the median trace, and the next-brightest
  trace below 0.3 of it). Where the log and the raw-header OBSCMNT disagree
  on the fiber, the one the data check confirms is used (APO 55754/28,
  55810/45; both are noted).
* `not_science`: the telescope was pointed off the plate field (so no plugged
  fiber saw its target), but the fiber that saw the star is not established:
  no trace stands out, or a second fiber is comparably bright, or the
  brightest trace is not the stated fiber, or the star's identity is in doubt
  (APO 55759/59-68: the log says HD 196944 but the pointing is 46 deg away from
  it, 15 arcsec from V2141 Cyg).
* `skip`: the raw header has no PLATEID (APO 55727/28, 55729/71), so almanac
  builds no fiber table for the exposure and there is nothing to override.

The CSV is rebuilt from scratch by the scripts in `datacheck/`, run from an
empty working directory (they write `notes/` there):

```
python datacheck/hdr_table.py            # raw-header table of the 17 nights
python datacheck/plugmap_check.py        # almanac plate id / fiber table per exposure (no database)
python resolve_targets.py --json notes/resolved.json   # star positions (also used for the separations)
python datacheck/fibercheck2.py datacheck/fibercheck_jobs.txt notes/fibercheck_v2.csv
python datacheck/join_inventory.py       # + datacheck/curated_from_logs.txt
python datacheck/build_handplaced_csv.py # writes handplaced_2011.csv
python make_overrides.py --write-yaml
```

`datacheck/curated_from_logs.txt` is the hand-made reading of the observers'
`.comments` files: for every exposure, the object and the fiber the log (or
only the header) names. Everything else is derived.

The data check (`datacheck/fibercheck.py`): for each exposure, the
up-the-ramp signal (last minus first read) of chip b from the raw `apR` file,
collapsed over columns 900-1150 to a profile across the fibers, summed in
+-2 rows around each trace of a flat of the same night and plate. Traces are
numbered 300 (top) down to 1, skipping numbers at gaps wider than 1.75 trace
spacings (dead fibers); a flat with too few traces is matched to the
APO 55755 numbering within +-3 px. The numbering was validated on APO 55755,
where all 22 exposures with star light have the brightest trace on the
fiber the log names (203, 192, 295).
