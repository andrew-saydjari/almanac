.. _almanac-changelog:

==========
Change Log
==========

* First change goes here.
* Added ``almanac add fibers`` to add fiber-to-target mappings to an existing almanac
  file that was created without ``--fibers``. The exposures already in the file are
  used to locate the confSummary/plugmap files, so raw exposure headers are not re-read.
* ``almanac add metadata`` now warns and exits without writing when the file has no
  fiber mappings for the selected nights (previously it silently wrote an empty
  ``meta`` group).
* Fixed the ``Exposure.prefix`` validator, which raised when ``prefix`` was passed
  explicitly as ``None``.
* ``almanac add metadata`` now also records the SDSS_ID position (``ra``, ``dec``,
  ``l``, ``b``, ``healpix``), ``n_associated``, unWISE and GLIMPSE photometry,
  Bailer-Jones et al. (2021) distances, Gaia DR3 synthetic photometry, Zhang, Green &
  Rix (2023) stellar parameters, and SDSS-IV APOGEE targeting flags for every source.
  ``healpy`` is a new dependency.
* The ``Source`` data model gains reddening fields (``ebv``, ``e_ebv``, ``ebv_flags`` and
  per-map ``ebv_*``/``e_ebv_*`` values). They are not yet populated.

