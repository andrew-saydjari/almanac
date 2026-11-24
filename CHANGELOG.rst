.. _almanac-changelog:

==========
Change Log
==========

Unreleased (fork)
-----------------
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
  docs build with ``-W``), myst-parser 5, isort 9, invoke 3, twine 7. ``docs/requirements.txt``
  follows the ``docs`` extra.
* Behavior change in ``add metadata`` from sdss-semaphore 0.2.9 (now locked; the range is
  ``>=0.2.8,<0.3``): it recognises more cartons, so ``/meta/sdss5_target_flags`` is wider
  (73 vs 71 bytes per row on the test nights) and ``unknown_carton_pks`` shrinks (116 -> 112
  entries). The first 71 bytes are unchanged (on the test nights 255 of 1,588 rows set bits
  in the new bytes). ``/meta`` tables built before and after are not column-compatible
  without zero-padding ``sdss5_target_flags``.
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

