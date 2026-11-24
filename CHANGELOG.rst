.. _almanac-changelog:

==========
Change Log
==========

Unreleased (fork)
-----------------
* ``lookup`` no longer prints debug ``Success``/``Failed`` lines, and ``lookup -O`` writes
  nights as ``almanac --fibers`` does, night attributes included (``io.write_night_attrs``).
* ``dump``: ``exposures``/``fibers`` (and ``postprocess``) work on files with one observatory;
  ``dump fibers`` rows carry ``mjd`` and ``fiber_map_key`` and a filled ``observatory`` (FITS
  output works); ``dump stars`` writes HDF5 and no longer pools fibers without an
  ``sdss_id`` into a fake star ``sdss_id = -1`` (``sdss_id`` 0, solar system, still counted).
* ``postprocess`` no longer stops with a ``NameError`` (``unknown_carton_pks``, left from the
  old metadata code; unknown cartons are recorded by ``add metadata`` on ``/meta``).
* ``config set`` converts the value to the key's declared type (int, float, bool, str, list)
  and refuses a value that does not fit; the hidden ``config update`` alias no longer
  crashes (it called the builtin ``set``). ``config.set_config_value``, ``coerce_value``.
* ``retarget_fibers`` works on FPS fibers: the row rewrite sets the FPS identifier fields
  (``twomass_designation``, ``catalogid``, ``ra``, ``dec``) instead of the plate-only
  ``target_ids``/``twomass_id`` (which raised a ValidationError), and the row is
  cross-matched as usual. No shipped entry is FPS, so no output changes.
* Stopping a build no longer corrupts the output file. The main command and ``add fibers``
  open the output only while writing it (one session per night, flushed and closed; HDF5
  metadata-cache evictions disabled within a session), so a run killed with SIGKILL leaves
  a file readable up to the last night written; workers are never forked while it is open
  (they no longer hold its lock) and die with the main process (Linux
  ``PR_SET_PDEATHSIG``). SIGTERM and SIGHUP are handled like SIGINT, and SIGINT is handled
  in background jobs too (``almanac.signals``): workers are killed, processed nights
  written, ``/missing_exposures`` and ``/run_summary`` written, exit status 128 + signal.
  Nights written by a killed run are listed in ``/missing_exposures_pending`` and processed
  again by the next ``--skip-existing`` run, so their missing-exposure rows are not lost.
* Main command: end-of-run summary and exit status. Every run logs the number of nights
  requested, skipped by ``--skip-existing``, with data, without data, failed and built
  without the operations database, and lists the failed nights with a category
  (``exception``, ``database_error``, ``write_failed``, ``pool_broken``); with ``--output``
  the same is written to the new ``/run_summary`` group (accumulated over runs). The exit
  status is now 3 when any night failed, and 4 when the operations database could not be
  queried for a night while the cross-match was requested; nights without data and
  ``--skip-existing`` skips stay 0. A cross-match without a database connection raises
  ``apogee.DatabaseUnavailableError`` instead of a ``KeyError``.
* ``almanac add fibers`` adds fiber mappings to an almanac file built without ``--fibers``,
  by running the same night pipeline as ``almanac --fibers`` (``apogee.process_night``:
  exposure overrides, fiber overrides including hand-placed targets and synthetic table
  ids, the ``sdss_id`` cross-match) on the exposures stored in the file, returned to their
  header values first (``overrides.rewind_exposure_overrides``). It writes fiber tables and
  night attributes with the same writers (``io.write_fibers``, ``io.write_night_attrs``).
  It never rewrites exposure columns or sequences: a night whose stored exposures or
  sequences differ from the re-processed ones (override list changed since the build, or
  the other ``--no-overrides`` setting) is refused and left untouched. It needs
  ``--output NEW.h5`` (copy, INPUT only read) or ``--in-place``. Honors ``--processes``,
  ``--query-workers``, ``--no-x-match``, ``--no-overrides``, ``--full-apogee-id-lookup``,
  ``--live-apogee-id-join``. No ``add fibres`` alias. Adapted from upstream sdss/almanac #66.
* ``io.read_exposures`` (also restores ``original_plate_id``/``original_config_id``),
  ``io.compare_exposures``, ``io.compare_sequences``, ``apogee.prepare_cross_match``.
* Documentation audit: every page (README, ``docs/``) checked against the code and
  rewritten where it was outdated or false (configuration path ``~/.almanac/config.yaml``
  and every configuration key, exit statuses, ``config set`` storing text, the
  ``dump``/``lookup``/``pgdump``/``pgrestore``/``postprocess``/``flag`` commands as they
  are, installing the fork rather than the PyPI package, Python 3.11). Documented what was
  missing: ``add fibers``, the sequences and how they are used, every exposure and fiber
  column (override columns, ``file_size``), the synthetic fiber-table IDs and what they mean
  for joins, the night attributes, every ``/meta`` attribute, the override file schema, and
  what ApogeeReduction.jl reads. ``docs/audit_docs.py`` regenerates the ground truth from the
  code and checks the documentation against it. API pages for every module.
* Help and docstring text corrected (no behavior change): ``--fibers``, ``-v``,
  ``--live-apogee-id-join`` (the lookup relation is a table), ``flag`` and ``dump visits``
  (not implemented), ``dump stars``, ``pgrestore`` (base tables only), and where overrides
  are applied (``apogee.process_night``).

0.5.3
-----
Sync with upstream sdss/almanac 0.5.2 (``a922dee``), merged into the fork; the version is one
past upstream's.

* ``almanac add metadata`` stops with exit code 1 and writes nothing (no ``/meta``, no output
  file, no checkpoint) when none of the selected nights has fiber mappings; previously it
  wrote an empty ``/meta``. Nights without fiber mappings are counted in a warning and skipped.
  Adapted from upstream #66; the check lives in ``almanac.metadata.build_metadata``.
* Fixed the ``Exposure.prefix`` validator, which raised when ``prefix`` was passed
  explicitly as ``None`` (upstream #66).
* The target cross-match is now ``almanac.apogee.cross_match_targets`` and the fiber writer
  ``almanac.io.write_fibers`` (function boundaries from upstream #66). Both keep the fork's
  bodies: targeted APOGEE ID lookups through the maintainers' table, best rank then smallest
  ``sdss_id``, no ``sdss_id`` for unplugged or off-target fibers, and exposure-override keys.
  Outputs are unchanged.
* Every dependency in ``pyproject.toml`` (runtime, ``dev``, ``docs``) now has a semver range:
  lower bound = the version tested (older deliberate minimums kept), upper bound = below the
  next major (next minor for 0.x; ``furo`` uses calendar versions and is uncapped).
  ``uv.lock`` is fully upgraded inside those ranges (``uv lock --upgrade``), which includes
  upstream's dependabot packages. Major moves of direct dependencies: astropy 8, peewee 4,
  sdssdb 1.x, rich 15, pytest 9, Sphinx 9 (the undocumented ``Sphinx<8`` cap is lifted; the
  docs build with ``-W``), myst-parser 5, isort 9, invoke 3, twine 7. ``sdss-semaphore`` is
  capped below 0.2.9: 0.2.9 encodes more cartons, which changes ``/meta/sdss5_target_flags``
  (measured). ``docs/requirements.txt`` follows the ``docs`` extra.
* Not taken from upstream: ``almanac add fibers`` as written upstream (and the readers
  ``io.read_exposures`` / ``io.read_sequences`` that only it uses), the unpopulated ``ebv*`` ``Source`` fields, and
  default-on value-added queries (the fork has them as ``add metadata --extended``).

0.4.3 (fork, unreleased changes)
--------------------------------

* Parallelism defaults are consistent and Slurm-aware. ``--processes``/``-p`` (main command,
  ``postprocess``) defaults to the CPU budget: ``SLURM_CPUS_ON_NODE`` in a Slurm job (or the
  CPU affinity if smaller), else ``sched_getaffinity``, else ``os.cpu_count()``; the main
  command never starts more workers than observatory/MJD pairs. ``--query-workers`` (main
  command, ``add metadata``; default ``catalog_query_max_workers`` = 4) caps how many workers
  hold a database connection at once. The resolved numbers and their source are logged.
  ``almanac add metadata --p`` (ignored for a long time) is removed and is now rejected as an
  unknown option; use ``--query-workers``. Nothing in the ApogeeReduction.jl pipelines used it
  (they call the main ``almanac`` command with ``-p 12``, which is unchanged).
* ``almanac add metadata --extended`` (opt-in) writes 79 value-added columns after the
  default ones: SDSS_ID position (``ra``, ``dec``, ``l``, ``b``, ``healpix``),
  ``n_associated``, unWISE and GLIMPSE photometry, Bailer-Jones et al. (2021) distances,
  Gaia DR3 synthetic photometry, Zhang, Green & Rix (2023) parameters and SDSS-IV APOGEE
  targeting flags. Adapted from upstream sdss/almanac ``a922dee`` (Andy Casey); upstream's
  never-filled ``ebv*`` columns are left out. ``almanac pgdump --extended`` also dumps the
  tables it reads. ``healpy`` is a new dependency.
* Every module almanac imports is now a declared dependency (``peewee``, ``pydantic-core``,
  ``pyerfa``, ``pyyaml``, ``scipy``, ``typing-extensions`` were only transitive); a test
  checks it, and CI runs the unit tests.

