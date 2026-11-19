.. _almanac-changelog:

==========
Change Log
==========

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

