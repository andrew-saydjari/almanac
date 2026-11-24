.. _almanac-changelog:

==========
Change Log
==========

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
* Lock refresh of upstream's dependabot packages, each to the latest version the declared
  constraints allow (``uv lock --upgrade-package``; nothing else upgraded): requests 2.34.2,
  pygments 2.21.0, pytest 9.2.0 (with pytest-asyncio 1.5.0), urllib3 2.8.0, idna 3.20,
  cryptography 50.0.2, pillow 13.0.0, soupsieve 4.1.3 (2.10 on Python < 3.11.5, which
  soupsieve 4 does not support).
* Not taken from upstream: ``almanac add fibers`` (and the readers ``io.read_exposures`` /
  ``io.read_sequences`` that only it uses), the unpopulated ``ebv*`` ``Source`` fields, and
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

