# The "robots not moved" rule

`robots_not_moved.py` derives, from operations logs, the FPS-era APOGEE
exposures whose header records a newly loaded configuration **B** while the
fiber positioners (robots) had not moved and were still at the previous
configuration **A**. The light in those fibers is starlight from A's
targets (or sky), not from the targets the header names.

Its output is the generated block of
`src/almanac/etc/exposure_overrides.yaml` (between the
`BEGIN robots-not-moved` / `END robots-not-moved` markers): one `not_science`
entry per observatory / MJD / (A, B) pair, with `recorded_config` = B and
`actual_config` = A, plus the fiber-level data-check verdict of each exposure
in the entry's `reason`. Do not edit that block by hand; regenerate it.

## The rule

For every FPS object exposure, with start time `t0` (the raw-header
`DATE-OBS`/UT of the exposure) and the jaeger actor log of that night:

1. **Configuration loaded:** take the last `configuration_loaded=<config>,<design>,...`
   record before `t0`. It must be less than 60 minutes before `t0`.
2. **Robots not moved:** no `Starting trajectory` record lies between that load and `t0`.
3. **A exists and differs:** the last `Starting trajectory` before `t0`
   (earlier that night) followed a `configuration_loaded` of a *different
   design*. That configuration is A, where the robots still are. B is the
   configuration of step 1 (it equals the header `CONFIGID`).
4. **Targets differ:** the confSummary files of B and A (sdsscore, flavours
   `FS > S > "" > F`, as almanac reads them) assign a different catalogid to
   at least one APOGEE fiber (assigned fibers only; a fiber assigned in one
   and not the other counts as different). Exposures where A and B have the
   same APOGEE assignment are harmless (3,403 of the 3,501 that pass steps
   1-3; one more, LCO 59860/17, has no confSummary for A) and are not listed.

Exclusions:

* **LCO before MJD 60000.** Header and log clocks did not agree at LCO then
  (robot trajectories appear in the middle of normal exposures), and the
  fiber-level data check shows that the signature is false there (LCO 59860
  exposures 12-14 follow B). All 3 such candidates are excluded.
* **Any exposure whose data check shows the light follows B** (`MATCHES_B`).
  None remains after the LCO exclusion.

Exposures with extra log flags are *kept* and the entry says so: robots also
folded at the start (fibers at neither configuration), or a trajectory
started during the exposure (mixed light). They are bad either way.

Not covered by this rule (separate cases, not in the generated block): a
configuration loaded *after* the exposure started (e.g. LCO 60425/21), and
robots folded with no new configuration (e.g. APO 61058/16).

## Steps and inputs

| step | command | inputs | output |
|---|---|---|---|
| 1 | `state` | jaeger log extract (`remote_extract_jaeger.sh`, run at Utah); exposure start times CSV `tele,mjd,expnum,t0_ut,dur_s,hdr_config` (from the raw headers; `dur_s` = NREAD x 10.647 s) | robot state per exposure where steps 1-3 fire |
| 2 | `cfgdiff` | step 1 output; sdsscore `summary_files`; optionally the release exposure table (`tele,mjd,expnum,eclass,nrows`) and the data-check verdicts (`tele,mjd,expnum,group,verdict,verdict_note`) | `robots_not_moved_candidates.csv` (with `--differing-only`) |
| 3 | `overrides` | `robots_not_moved_candidates.csv` | `--write-yaml`: the generated block; `--list-csv`: one row per listed exposure |

Only step 3 is needed to regenerate the override entries:

```bash
python scripts/robots_not_moved/robots_not_moved.py overrides --write-yaml --list-csv listed.csv
```

The jaeger (tron) logs are kept at Utah
(`sdss51/sdsswork/data/logs/{apo,lco}/tron/jaeger/`, APO from 2021-08,
LCO from 2022-08) and are **not** in this repository. Steps 1-2 were run on
2026-11-19 over every FPS-era object exposure (97,000 exposures on 2,194
nights); the shipped `robots_not_moved_candidates.csv` is their output
restricted to exposures whose targets differ (97 rows). Re-running steps 1-2
on that extract reproduces the earlier analysis tables exactly
(`robots_not_moved`, `folded_at_start`, trajectory flags, A and the
fiber-difference counts for all 3,501 flagged exposures). Only jaeger
keyword records are used: no observer text and no names.

The `data_check*` columns come from the fiber-level check of 2026-11-19
(fiber light against catalog H of the A and B targets, Spearman rank
correlation on the fibers whose target differs, plus a lit/unlit check on
fibers that are a star in one configuration and sky in the other). Values:
`MATCHES_A`, `MATCHES_B`, `UNKNOWN` (undetermined), `NOT_TESTED` (the
exposure was NOLIGHT in 0.4.0, or not in the 0.4.0 release, so it was not
tested).

## Result (2026-11-19)

94 exposures in 55 entries: APO 71 exposures on 29 nights, LCO 23 on 16
nights. Data check: 38 `MATCHES_A`, 4 `UNKNOWN`, 52 `NOT_TESTED` (30 NOLIGHT
in 0.4.0, 22 not in the 0.4.0 release). Excluded: LCO 59860/12-14.

## Why `not_science`, and what comes later

For 0.4.1 these exposures are marked bad, as the conservative choice. The
light is real starlight from configuration A, and the data check recovered
the true target for most affected fibers. After 0.4.1 we want to come back
and try to save some by switching entries to `use_config <A>` (the entry's
`actual_config`) for the **whole exposure**: fibers that B treats as sky
carried A's stars, so per-fiber fixes would leave B's sky fibers wrong.
Only exposures whose data check says `MATCHES_A` are candidates.
