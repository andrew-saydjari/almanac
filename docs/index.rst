almanac
=======

``almanac`` scrapes headers from raw APOGEE image files and cross-matches them against the
SDSS database to build a summary of everything observed with an APOGEE instrument: the
exposures of every night, the sequences of exposures that form visits, and the
fiber-to-target mapping of every plate and FPS configuration, with an SDSS identifier
(``sdss_id``) for every target.

This documentation is for the ``andrew-saydjari/almanac`` fork, version 0.5.3: upstream
sdss/almanac 0.5.2 plus the fork's changes (exposure overrides and hand-placed targets,
off-target fibers, the APOGEE ID cross-match through the maintainers' lookup table,
``almanac add fibers``, a resumable ``almanac add metadata``, and Slurm-aware
parallelism).

.. image:: https://github.com/sdss/almanac/blob/main/docs/almanac-example-1.gif?raw=true
   :alt: almanac example

Quick Start
-----------

.. code-block:: bash

   # Install the fork
   git clone https://github.com/andrew-saydjari/almanac.git
   cd almanac
   uv sync --frozen

   # Last night's exposures, with details
   uv run almanac --mjd -1 -vv

   # Last night's exposures and fiber mappings, written to a file
   uv run almanac --mjd -1 --fibers --output results.h5

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   installation
   user-guide
   cli-reference
   configuration
   data-formats
   exposure-overrides
   faq

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   api-reference
   development

.. toctree::
   :maxdepth: 1
   :caption: API Documentation

   api/modules

Basic Usage
-----------

The current night at both observatories:

.. code-block:: bash

   almanac

One night, with details:

.. code-block:: bash

   almanac -vv --mjd 60000

With fiber mappings, written to a file:

.. code-block:: bash

   almanac --mjd 60000 --fibers --output results.h5

Key Features
------------

- **Flexible night selection**: single MJDs or dates, ranges, and explicit lists
- **Both observatories**: Apache Point Observatory (APO) and Las Campanas Observatory (LCO)
- **Both eras**: SDSS-IV plates and the SDSS-V Fiber Positioning System (FPS)
- **Fiber mappings**: every fiber's target, cross-matched to an ``sdss_id``
- **Missing-exposure report**: every gap, with a reason
- **Exposure overrides**: explicit, version-controlled corrections with their evidence
- **Structured output**: one HDF5 file, updated night by night and resumable
- **Catalog metadata**: astrometry, photometry and targeting flags per ``sdss_id``
- **Parallel**: worker processes with a cap on database connections

Indices and Tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
