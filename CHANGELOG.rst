.. _almanac-changelog:

==========
Change Log
==========

* Parallelism defaults are consistent and Slurm-aware. ``--processes``/``-p`` (main command,
  ``postprocess``) defaults to the CPU budget: ``SLURM_CPUS_ON_NODE`` in a Slurm job (or the
  CPU affinity if smaller), else ``sched_getaffinity``, else ``os.cpu_count()``; the main
  command never starts more workers than observatory/MJD pairs. ``--query-workers`` (main
  command, ``add metadata``; default ``catalog_query_max_workers`` = 4) caps how many workers
  hold a database connection at once. The resolved numbers and their source are logged.
  ``almanac add metadata --p`` now warns that it is deprecated (use ``--query-workers``).
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

