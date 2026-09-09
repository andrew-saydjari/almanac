#from itertools import batched
import os
import numpy as np
from tqdm import tqdm
from typing import List
from peewee import JOIN, BigIntegerField

from itertools import islice
import concurrent.futures

def batched(iterable, n):
    it = iter(iterable)
    while True:
        batch = tuple(islice(it, n))
        if not batch:
            return
        yield batch

def merge_dicts(*dicts):
    keys = dicts[0].keys()
    return {
        k: next((d[k] for d in dicts if d.get(k, None) is not None), None)
        for k in keys
    }


def merge_missing(existing: dict, new: dict) -> dict:
    """
    Update `existing` in place with any keys from `new` that are absent or
    `None` in `existing`, and return it. Unlike `merge_dicts`, the union of
    keys is kept, so value-added catalog rows can extend a source record.
    """
    for key, value in new.items():
        if existing.get(key, None) is None:
            existing[key] = value
    return existing


# HEALPix resolution used for the `healpix` field (RING ordering, lon/lat input).
HEALPIX_NSIDE = 128

# Vega zero-point offsets applied when converting unWISE fluxes (nMgy) to
# magnitudes. See https://catalog.unwise.me/catalogs.html ("Flux Scale").
UNWISE_VEGA_OFFSETS = {"w1": 4e-3, "w2": 32e-3}

# SDSS-IV APOGEE targeting bitmasks that are stored as *signed* 32-bit
# integers in catalogdb.allstar_dr17_synspec_rev1 (bit 31 set -> negative).
SDSS4_APOGEE_INT32_FLAG_FIELDS = (
    "sdss4_apogee_target1_flags",
    "sdss4_apogee_target2_flags",
    "sdss4_apogee2_target1_flags",
    "sdss4_apogee2_target2_flags",
    "sdss4_apogee2_target3_flags",
    "sdss4_apogee_extra_target_flags",
)


def to_unsigned_int32(value):
    """
    Re-interpret a signed 32-bit integer bitmask as unsigned (`None` passes
    through). Positive values are unchanged; negative values gain 2**32.
    """
    if value is None:
        return None
    return int(value) & 0xFFFFFFFF


def unwise_flux_to_mag(flux, dflux, band: str):
    """
    Convert unWISE fluxes (Vega nMgy) to Vega magnitudes and errors.

    :param flux:
        Flux (or array of fluxes) in nMgy.
    :param dflux:
        Statistical flux uncertainty (or array) in nMgy.
    :param band:
        Either "w1" or "w2" (selects the Vega zero-point offset).

    :returns:
        A two-length tuple of (mag, e_mag) arrays; NaN where the flux is
        missing or non-positive.
    """
    offset = UNWISE_VEGA_OFFSETS[band]
    flux = np.array(flux, dtype=float, ndmin=1)
    dflux = np.array(dflux, dtype=float, ndmin=1)
    ok = np.isfinite(flux) & (flux > 0)
    safe_flux = np.where(ok, flux, 1.0)
    mag = np.where(ok, -2.5 * np.log10(safe_flux) + 22.5 - offset, np.nan)
    e_mag = np.where(ok, (2.5 / np.log(10)) * dflux / safe_flux, np.nan)
    return (mag, e_mag)


def compute_galactic_coordinates(ra, dec):
    """
    Convert ICRS (ra, dec) [deg] to Galactic (l, b) [deg]. Non-finite inputs
    give NaN outputs.
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    ra = np.array(ra, dtype=float, ndmin=1)
    dec = np.array(dec, dtype=float, ndmin=1)
    l = np.full(ra.shape, np.nan)
    b = np.full(ra.shape, np.nan)
    ok = np.isfinite(ra) & np.isfinite(dec)
    if np.any(ok):
        galactic = SkyCoord(ra=ra[ok] * u.deg, dec=dec[ok] * u.deg, frame="icrs").galactic
        l[ok] = galactic.l.deg
        b[ok] = galactic.b.deg
    return (l, b)


def compute_healpix(ra, dec, nside: int = HEALPIX_NSIDE):
    """
    Compute HEALPix pixel indices (RING ordering) for ICRS (ra, dec) [deg].
    Non-finite inputs give -1.
    """
    from healpy import ang2pix

    ra = np.array(ra, dtype=float, ndmin=1)
    dec = np.array(dec, dtype=float, ndmin=1)
    healpix = np.full(ra.shape, -1, dtype=np.int64)
    ok = np.isfinite(ra) & np.isfinite(dec)
    if np.any(ok):
        healpix[ok] = ang2pix(nside, ra[ok], dec[ok], lonlat=True)
    return healpix


def add_derived_quantities(meta: dict) -> dict:
    """
    Fill in quantities derived from the queried catalog values, in place:

    - `ra`/`dec` fall back to the Gaia DR3 position when the SDSS_ID position
      is missing;
    - `l`, `b`, and `healpix` are computed from `ra`/`dec`;
    - unWISE magnitudes (and errors) are computed from fluxes;
    - Zhang, Green & Rix (2023) temperatures are converted from kK to K;
    - SDSS-IV APOGEE signed 32-bit bitmasks are re-interpreted as unsigned.

    :param meta:
        Dictionary of source dictionaries, keyed by `sdss_id`.
    """
    if not meta:
        return meta

    sdss_ids = list(meta.keys())

    def _float(item, key):
        value = item.get(key, None)
        return np.nan if value is None else float(value)

    for sdss_id in sdss_ids:
        item = meta[sdss_id]
        if item.get("ra", None) is None:
            item["ra"] = item.get("gaia_ra", None)
        if item.get("dec", None) is None:
            item["dec"] = item.get("gaia_dec", None)

    ra = np.array([_float(meta[s], "ra") for s in sdss_ids])
    dec = np.array([_float(meta[s], "dec") for s in sdss_ids])
    l, b = compute_galactic_coordinates(ra, dec)
    healpix = compute_healpix(ra, dec)

    for band in ("w1", "w2"):
        flux = np.array([_float(meta[s], f"{band}_flux") for s in sdss_ids])
        dflux = np.array([_float(meta[s], f"{band}_dflux") for s in sdss_ids])
        mag, e_mag = unwise_flux_to_mag(flux, dflux, band)
        for i, sdss_id in enumerate(sdss_ids):
            meta[sdss_id][f"{band}_mag"] = mag[i]
            meta[sdss_id][f"e_{band}_mag"] = e_mag[i]

    for i, sdss_id in enumerate(sdss_ids):
        item = meta[sdss_id]
        item["l"] = l[i]
        item["b"] = b[i]
        item["healpix"] = int(healpix[i])

        # The ZGR catalog stores temperatures in kK.
        for key in ("zgr_teff", "zgr_e_teff"):
            if item.get(key, None) is not None:
                item[key] = 1000 * float(item[key])

        for key in SDSS4_APOGEE_INT32_FLAG_FIELDS:
            if item.get(key, None) is not None:
                item[key] = to_unsigned_int32(item[key])

    return meta


def _copy_sdss_ids(database, table_name: str, sdss_ids):
    """
    Bulk-load SDSS IDs into a (temporary) table via PostgreSQL COPY,
    supporting both psycopg2 and psycopg (v3) cursor APIs.
    """
    conn = database.connection()
    cursor = conn.cursor()
    if hasattr(cursor, "copy_from"):
        # psycopg2
        import io as _io
        buf = _io.StringIO("".join(f"{sdss_id}\n" for sdss_id in sdss_ids))
        cursor.copy_from(buf, table_name, columns=("sdss_id",))
    else:
        # psycopg (v3)
        with cursor.copy(f"COPY {table_name} (sdss_id) FROM STDIN") as copy:
            for sdss_id in sdss_ids:
                copy.write_row((sdss_id,))
    conn.commit()


def query_targeting(sdss_ids: List[int], **kwargs):
    """
    Query the SDSS database for targeting (carton) information.

    :param sdss_ids: List[int]
        List of SDSS IDs to query
    """
    from almanac.database import catalogdb as cdb, targetdb as tdb

    cdb.database.execute_sql("DROP TABLE IF EXISTS tmp_sdss_ids")
    cdb.database.execute_sql(
        "CREATE TEMP TABLE tmp_sdss_ids (sdss_id BIGINT PRIMARY KEY)"
    )

    _copy_sdss_ids(cdb.database, 'tmp_sdss_ids', sdss_ids)

    class Source(cdb.CatalogdbModel):

        sdss_id = BigIntegerField(primary_key=True)

        class Meta:
            table_name = 'tmp_sdss_ids'
            schema = None

    q_cartons = (
        Source
        .select(
            Source.sdss_id,
            tdb.CartonToTarget.carton_pk,
        )
        .join(cdb.SDSS_ID_flat, on=(Source.sdss_id == cdb.SDSS_ID_flat.sdss_id))
        .join(tdb.Target, on=(cdb.SDSS_ID_flat.catalogid == tdb.Target.catalogid))
        .join(tdb.CartonToTarget, on=(tdb.Target.pk == tdb.CartonToTarget.target_pk))
        .tuples()
    )

    yield from q_cartons


def query(sdss_ids: List[int], batch_size: int = 10_000, tqdm_kwds=None, max_workers=None):
    """
    Query the SDSS database for targeting, astrometry, and photometry information.

    Parameters
    ----------
    sdss_ids : List[int]
        List of SDSS IDs to query
    batch_size : int, optional
        Number of IDs per batch (default: 10,000)
    max_workers : int, optional
        Number of worker processes (default: the `catalog_query_max_workers`
        configuration setting). Each worker opens its own database connection;
        keep this low when connecting through a single SSH tunnel.

    Yields
    ------
    dict
        Dictionary containing catalog data for each SDSS ID
    """
    from almanac import config

    if max_workers is None:
        max_workers = int(getattr(config, "catalog_query_max_workers", 4))
    max_workers = max(1, min(max_workers, os.cpu_count() or 1))

    meta = {}
    tqdm_kwds = tqdm_kwds or {}
    with tqdm(desc="Querying catalog", total=len(sdss_ids), **tqdm_kwds) as pb:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_query_catalog, batch, i)
                for i, batch in enumerate(batched(sorted(sdss_ids), batch_size))
            ]
            for future in concurrent.futures.as_completed(futures):
                meta.update(future.result())
                pb.update(batch_size)

    return meta


def _query_catalog(sdss_ids: List[int], suffix=""):
    """
    Query the SDSS database for targeting, astrometry, and photometry
    information, retrying (with reconnect and backoff) on transient database
    connection errors.

    Parameters
    ----------
    sdss_ids : List[int]
        List of SDSS IDs to query
    """
    from almanac.retry import retry_on_database_error

    return retry_on_database_error(_query_catalog_once)(sdss_ids, suffix=suffix)


def _select_sdss_id_position(Source, cdb):
    """SDSS_ID position from `sdss_id_stacked` (one row per `sdss_id`)."""
    return (
        Source
        .select(
            Source.sdss_id,
            cdb.SDSS_ID_stacked.ra_sdss_id.alias("ra"),
            cdb.SDSS_ID_stacked.dec_sdss_id.alias("dec"),
        )
        .join(cdb.SDSS_ID_stacked, on=(Source.sdss_id == cdb.SDSS_ID_stacked.sdss_id))
    )


def _select_n_associated(Source, cdb):
    """
    Number of SDSS_IDs associated with the primary (`rank == 1`) catalogid,
    from `sdss_id_flat`. Ordered so the highest `version_id` merges first.
    """
    return (
        Source
        .select(
            Source.sdss_id,
            cdb.SDSS_ID_flat.n_associated,
        )
        .join(cdb.SDSS_ID_flat, on=(Source.sdss_id == cdb.SDSS_ID_flat.sdss_id))
        .where(cdb.SDSS_ID_flat.rank == 1)
        .order_by(Source.sdss_id.asc(), cdb.SDSS_ID_flat.version_id.desc())
    )


def _select_unwise(Source, cdb):
    """unWISE fluxes and flags, matched through `sdss_id_to_catalog.unwise`."""
    return (
        Source
        .select(
            Source.sdss_id,
            cdb.unWISE.flux_w1.alias("w1_flux"),
            cdb.unWISE.dflux_w1.alias("w1_dflux"),
            cdb.unWISE.fracflux_w1.alias("w1_frac"),
            cdb.unWISE.flux_w2.alias("w2_flux"),
            cdb.unWISE.dflux_w2.alias("w2_dflux"),
            cdb.unWISE.fracflux_w2.alias("w2_frac"),
            cdb.unWISE.flags_unwise_w1.alias("w1uflags"),
            cdb.unWISE.flags_unwise_w2.alias("w2uflags"),
            cdb.unWISE.flags_info_w1.alias("w1aflags"),
            cdb.unWISE.flags_info_w2.alias("w2aflags"),
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(cdb.unWISE, on=(cdb.unWISE.unwise_objid == cdb.SDSS_ID_To_Catalog.unwise))
    )


def _select_glimpse(Source, cdb):
    """GLIMPSE 4.5um photometry, matched through `sdss_id_to_catalog.glimpse`."""
    return (
        Source
        .select(
            Source.sdss_id,
            cdb.GLIMPSE.mag4_5,
            cdb.GLIMPSE.d4_5m,
            cdb.GLIMPSE.rms_f4_5,
            cdb.GLIMPSE.sqf_4_5,
            cdb.GLIMPSE.mf4_5,
            cdb.GLIMPSE.csf,
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(cdb.GLIMPSE, on=(cdb.GLIMPSE.pk == cdb.SDSS_ID_To_Catalog.glimpse))
    )


def _select_bailer_jones(Source, cdb):
    """Bailer-Jones et al. (2021) distances, matched on the Gaia DR3 source_id."""
    return (
        Source
        .select(
            Source.sdss_id,
            cdb.BailerJonesEDR3.r_med_geo,
            cdb.BailerJonesEDR3.r_lo_geo,
            cdb.BailerJonesEDR3.r_hi_geo,
            cdb.BailerJonesEDR3.r_med_photogeo,
            cdb.BailerJonesEDR3.r_lo_photogeo,
            cdb.BailerJonesEDR3.r_hi_photogeo,
            cdb.BailerJonesEDR3.flag.alias("bailer_jones_flags"),
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(cdb.BailerJonesEDR3, on=(cdb.BailerJonesEDR3.source_id == cdb.SDSS_ID_To_Catalog.gaia_dr3_source))
    )


def _select_gaia_synthetic_photometry(Source, cdb):
    """Gaia DR3 synthetic photometry (GSPC), matched on the Gaia DR3 source_id."""
    G = cdb.Gaia_dr3_synthetic_photometry_gspc
    columns = [Source.sdss_id, G.c_star]
    for band in ("u_jkc", "b_jkc", "v_jkc", "r_jkc", "i_jkc", "u_sdss", "g_sdss", "r_sdss", "i_sdss", "z_sdss", "y_ps1"):
        columns.append(getattr(G, f"{band}_mag"))
        columns.append(getattr(G, f"{band}_flag").alias(f"{band}_mag_flag"))
    return (
        Source
        .select(*columns)
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(G, on=(G.source_id == cdb.SDSS_ID_To_Catalog.gaia_dr3_source))
    )


def _select_zhang_stellar_parameters(Source, cdb):
    """
    Zhang, Green & Rix (2023) stellar parameters from Gaia XP spectra, matched
    on the Gaia DR3 source_id. Temperatures are converted from kK to K in
    `add_derived_quantities`.
    """
    Z = cdb.Gaia_Stellar_Parameters
    return (
        Source
        .select(
            Source.sdss_id,
            Z.stellar_params_est_teff.alias("zgr_teff"),
            Z.stellar_params_err_teff.alias("zgr_e_teff"),
            Z.stellar_params_est_logg.alias("zgr_logg"),
            Z.stellar_params_err_logg.alias("zgr_e_logg"),
            Z.stellar_params_est_fe_h.alias("zgr_fe_h"),
            Z.stellar_params_err_fe_h.alias("zgr_e_fe_h"),
            Z.stellar_params_est_e.alias("zgr_e"),
            Z.stellar_params_err_e.alias("zgr_e_e"),
            Z.stellar_params_est_parallax.alias("zgr_plx"),
            Z.stellar_params_err_parallax.alias("zgr_e_plx"),
            Z.teff_confidence.alias("zgr_teff_confidence"),
            Z.logg_confidence.alias("zgr_logg_confidence"),
            Z.feh_confidence.alias("zgr_fe_h_confidence"),
            Z.ln_prior.alias("zgr_ln_prior"),
            Z.chi2_opt.alias("zgr_chi2"),
            Z.quality_flags.alias("zgr_quality_flags"),
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(Z, on=(Z.gdr3_source_id == cdb.SDSS_ID_To_Catalog.gaia_dr3_source))
    )


def _select_sdss4_apogee_targeting(Source, cdb):
    """
    SDSS-IV APOGEE (DR17 allStar) targeting bitmasks, matched through
    `sdss_id_to_catalog.allstar_dr17_synspec_rev1` (the apstar_id).
    """
    Star = cdb.AllStar_DR17_synspec_rev1
    return (
        Source
        .select(
            Source.sdss_id,
            Star.apogee_target1.alias("sdss4_apogee_target1_flags"),
            Star.apogee_target2.alias("sdss4_apogee_target2_flags"),
            Star.apogee2_target1.alias("sdss4_apogee2_target1_flags"),
            Star.apogee2_target2.alias("sdss4_apogee2_target2_flags"),
            Star.apogee2_target3.alias("sdss4_apogee2_target3_flags"),
            Star.memberflag.alias("sdss4_apogee_member_flags"),
            Star.extratarg.alias("sdss4_apogee_extra_target_flags"),
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(Star, on=(Star.apstar_id == cdb.SDSS_ID_To_Catalog.allstar_dr17_synspec_rev1))
    )


# Value-added catalogs joined to each batch of sources. Each builder returns a
# peewee query selecting `sdss_id` plus columns aliased to `Source` field names.
VALUE_ADDED_SELECTS = (
    ("sdss_id_stacked", _select_sdss_id_position),
    ("sdss_id_flat", _select_n_associated),
    ("unwise", _select_unwise),
    ("glimpse", _select_glimpse),
    ("bailer_jones_edr3", _select_bailer_jones),
    ("gaia_dr3_synthetic_photometry_gspc", _select_gaia_synthetic_photometry),
    ("gaia_stellar_parameters", _select_zhang_stellar_parameters),
    ("allstar_dr17_synspec_rev1", _select_sdss4_apogee_targeting),
)


def _merge_value_added_catalogs(Source, cdb, meta: dict) -> dict:
    """
    Run each value-added catalog query against the temporary source table and
    merge the rows into `meta` (only for `sdss_id`s already present).

    A catalog that is unavailable on this database host (e.g., the Zhang et al.
    table exists on operations but not on pipelines) is logged and skipped
    rather than failing the whole batch.
    """
    from peewee import ProgrammingError
    from almanac.logger import logger

    for table_name, select in VALUE_ADDED_SELECTS:
        try:
            for item in select(Source, cdb).dicts().iterator():
                sdss_id = item.pop("sdss_id")
                if sdss_id in meta:
                    merge_missing(meta[sdss_id], item)
        except (ProgrammingError, AttributeError) as e:
            # A failed statement aborts the transaction; roll back so the
            # remaining queries on this connection can proceed.
            try:
                cdb.database.rollback()
            except Exception:
                pass
            logger.warning(
                f"Skipping catalogdb.{table_name} for this batch "
                f"(table or column unavailable on this host?): {e}"
            )
    return meta


def _query_catalog_once(sdss_ids: List[int], suffix=""):
    """
    Query the SDSS database for targeting, astrometry, and photometry information.

    Uses a temporary table for efficient querying of large ID lists.

    Parameters
    ----------
    sdss_ids : List[int]
        List of SDSS IDs to query
    """

    from almanac.database import catalogdb as cdb, targetdb as tdb

    # The temp table may survive a failed earlier attempt if the session is
    # still alive (a reconnect drops it automatically).
    cdb.database.execute_sql(f"DROP TABLE IF EXISTS tmp_sdss_ids{suffix}")
    cdb.database.execute_sql(
        f"CREATE TEMP TABLE tmp_sdss_ids{suffix} (sdss_id BIGINT PRIMARY KEY)"
    )
    _copy_sdss_ids(cdb.database, f"tmp_sdss_ids{suffix}", sdss_ids)

    cdb.database.execute_sql("SET enable_seqscan = off")
    cdb.database.execute_sql("SET enable_hashjoin = off")
    cdb.database.execute_sql("SET enable_mergejoin = off")
    cdb.database.execute_sql(f"ANALYZE tmp_sdss_ids{suffix}")

    class Source(cdb.CatalogdbModel):

        sdss_id = BigIntegerField(primary_key=True)

        class Meta:
            table_name = f'tmp_sdss_ids{suffix}'
            schema = None

    # Query astrometry and photometry using temp table
    q = (
        Source
        .select(
            cdb.SDSS_ID_To_Catalog.sdss_id,
            # TODO: catalogid and version_id may not be reliable in that we
            #       will get multiple versions per sdss_id. There is no good
            #       way to handle this because some other surveys are crossmatched
            #       to an early version, and some to a later version.
            #       We need a thinko here. We need Zach Way.
            cdb.SDSS_ID_To_Catalog.catalogid,
            cdb.SDSS_ID_To_Catalog.version_id,
            cdb.SDSS_ID_To_Catalog.lead,
            cdb.SDSS_ID_To_Catalog.allstar_dr17_synspec_rev1,
            cdb.SDSS_ID_To_Catalog.allwise,
            cdb.SDSS_ID_To_Catalog.catwise,
            cdb.SDSS_ID_To_Catalog.catwise2020,
            cdb.SDSS_ID_To_Catalog.gaia_dr2_source,
            cdb.SDSS_ID_To_Catalog.gaia_dr3_source,
            cdb.SDSS_ID_To_Catalog.glimpse,
            cdb.SDSS_ID_To_Catalog.guvcat,
            cdb.SDSS_ID_To_Catalog.panstarrs1,
            cdb.SDSS_ID_To_Catalog.ps1_g18,
            cdb.SDSS_ID_To_Catalog.sdss_dr13_photoobj,
            cdb.SDSS_ID_To_Catalog.sdss_dr17_specobj,
            cdb.SDSS_ID_To_Catalog.skymapper_dr2,
            cdb.SDSS_ID_To_Catalog.supercosmos,
            cdb.SDSS_ID_To_Catalog.tic_v8,
            cdb.SDSS_ID_To_Catalog.twomass_psc,
            cdb.SDSS_ID_To_Catalog.tycho2,
            cdb.SDSS_ID_To_Catalog.unwise,
            cdb.Gaia_DR3.source_id.alias('gaia_source_id'),
            cdb.Gaia_DR3.ra.alias('gaia_ra'),
            cdb.Gaia_DR3.ra_error.alias('gaia_ra_error'),
            cdb.Gaia_DR3.dec.alias('gaia_dec'),
            cdb.Gaia_DR3.dec_error.alias('gaia_dec_error'),
            cdb.Gaia_DR3.parallax.alias('gaia_parallax'),
            cdb.Gaia_DR3.parallax_error.alias('gaia_parallax_error'),
            cdb.Gaia_DR3.pm.alias('gaia_pm'),
            cdb.Gaia_DR3.pmra.alias('gaia_pmra'),
            cdb.Gaia_DR3.pmra_error.alias('gaia_pmra_error'),
            cdb.Gaia_DR3.pmdec.alias('gaia_pmdec'),
            cdb.Gaia_DR3.pmdec_error.alias('gaia_pmdec_error'),
            cdb.Gaia_DR3.ruwe.alias('gaia_ruwe'),
            cdb.Gaia_DR3.duplicated_source.alias('gaia_duplicated_source'),
            cdb.Gaia_DR3.phot_g_mean_mag.alias('gaia_phot_g_mean_mag'),
            cdb.Gaia_DR3.phot_bp_mean_mag.alias('gaia_phot_bp_mean_mag'),
            cdb.Gaia_DR3.phot_rp_mean_mag.alias('gaia_phot_rp_mean_mag'),
            cdb.Gaia_DR3.phot_bp_rp_excess_factor.alias('gaia_phot_bp_rp_excess_factor'),
            cdb.Gaia_DR3.radial_velocity.alias('gaia_radial_velocity'),
            cdb.Gaia_DR3.radial_velocity_error.alias('gaia_radial_velocity_error'),
            cdb.Gaia_DR3.rv_nb_transits.alias('gaia_rv_nb_transits'),
            cdb.Gaia_DR3.rv_nb_deblended_transits.alias('gaia_rv_nb_deblended_transits'),
            cdb.Gaia_DR3.rv_visibility_periods_used.alias('gaia_rv_visibility_periods_used'),
            cdb.Gaia_DR3.rv_expected_sig_to_noise.alias('gaia_rv_expected_sig_to_noise'),
            cdb.Gaia_DR3.rv_renormalised_gof.alias('gaia_rv_renormalised_gof'),
            cdb.Gaia_DR3.rv_chisq_pvalue.alias('gaia_rv_chisq_pvalue'),
            cdb.Gaia_DR3.rv_time_duration.alias('gaia_rv_time_duration'),
            cdb.Gaia_DR3.rv_amplitude_robust.alias('gaia_rv_amplitude_robust'),
            cdb.Gaia_DR3.rv_template_teff.alias('gaia_rv_template_teff'),
            cdb.Gaia_DR3.rv_template_logg.alias('gaia_rv_template_logg'),
            cdb.Gaia_DR3.rv_template_fe_h.alias('gaia_rv_template_fe_h'),
            cdb.Gaia_DR3.rv_atm_param_origin.alias('gaia_rv_atm_param_origin'),
            cdb.Gaia_DR3.vbroad.alias('gaia_vbroad'),
            cdb.Gaia_DR3.vbroad_error.alias('gaia_vbroad_error'),
            cdb.Gaia_DR3.vbroad_nb_transits.alias('gaia_vbroad_nb_transits'),
            cdb.Gaia_DR3.grvs_mag.alias('gaia_grvs_mag'),
            cdb.Gaia_DR3.grvs_mag_error.alias('gaia_grvs_mag_error'),
            cdb.Gaia_DR3.grvs_mag_nb_transits.alias('gaia_grvs_mag_nb_transits'),
            cdb.Gaia_DR3.rvs_spec_sig_to_noise.alias('gaia_rvs_spec_sig_to_noise'),
            cdb.Gaia_DR3.teff_gspphot.alias('gaia_teff_gspphot'),
            cdb.Gaia_DR3.logg_gspphot.alias('gaia_logg_gspphot'),
            cdb.Gaia_DR3.mh_gspphot.alias('gaia_mh_gspphot'),
            cdb.Gaia_DR3.distance_gspphot.alias('gaia_distance_gspphot'),
            cdb.Gaia_DR3.azero_gspphot.alias('gaia_azero_gspphot'),
            cdb.Gaia_DR3.ag_gspphot.alias('gaia_ag_gspphot'),
            cdb.TwoMassPSC.designation.alias('twomass_designation'),
            cdb.TwoMassPSC.j_m.alias('twomass_j_m'),
            cdb.TwoMassPSC.j_cmsig.alias('twomass_j_cmsig'),
            cdb.TwoMassPSC.j_msigcom.alias('twomass_j_msigcom'),
            cdb.TwoMassPSC.j_snr.alias('twomass_j_snr'),
            cdb.TwoMassPSC.h_m.alias('twomass_h_m'),
            cdb.TwoMassPSC.h_cmsig.alias('twomass_h_cmsig'),
            cdb.TwoMassPSC.h_msigcom.alias('twomass_h_msigcom'),
            cdb.TwoMassPSC.h_snr.alias('twomass_h_snr'),
            cdb.TwoMassPSC.k_m.alias('twomass_k_m'),
            cdb.TwoMassPSC.k_cmsig.alias('twomass_k_cmsig'),
            cdb.TwoMassPSC.k_msigcom.alias('twomass_k_msigcom'),
            cdb.TwoMassPSC.k_snr.alias('twomass_k_snr'),
            cdb.TwoMassPSC.ph_qual.alias('twomass_ph_qual'),
            cdb.TwoMassPSC.rd_flg.alias('twomass_rd_flg'),
            cdb.TwoMassPSC.bl_flg.alias('twomass_bl_flg'),
            cdb.TwoMassPSC.cc_flg.alias('twomass_cc_flg'),
        )
        .join(cdb.SDSS_ID_To_Catalog, on=(Source.sdss_id == cdb.SDSS_ID_To_Catalog.sdss_id))
        .join(cdb.Gaia_DR3, join_type=JOIN.LEFT_OUTER, on=(cdb.Gaia_DR3.source_id == cdb.SDSS_ID_To_Catalog.gaia_dr3_source))
        .switch(cdb.SDSS_ID_To_Catalog)
        .join(cdb.TwoMassPSC, join_type=JOIN.LEFT_OUTER, on=(cdb.TwoMassPSC.pts_key == cdb.SDSS_ID_To_Catalog.twomass_psc))
        .switch(cdb.SDSS_ID_To_Catalog)
        .dicts()
    )

    meta = {}
    for item in q.iterator():
        sdss_id = item['sdss_id']
        meta[sdss_id] = merge_dicts(item, meta.get(sdss_id, {}))

    _merge_value_added_catalogs(Source, cdb, meta)
    add_derived_quantities(meta)

    q_cartons = (
        Source
        .select(
            Source.sdss_id,
            tdb.CartonToTarget.carton_pk,
        )
        .join(cdb.SDSS_ID_flat, on=(Source.sdss_id == cdb.SDSS_ID_flat.sdss_id))
        .join(tdb.Target, on=(cdb.SDSS_ID_flat.catalogid == tdb.Target.catalogid))
        .join(tdb.CartonToTarget, on=(tdb.Target.pk == tdb.CartonToTarget.target_pk))
        .tuples()
    )
    cartons = {}
    for sdss_id, carton_pks in q_cartons:
        try:
            cartons[sdss_id].add(carton_pks)
        except KeyError:
            cartons[sdss_id] = {carton_pks}

    for sdss_id in meta.keys():
        meta[sdss_id]["carton_pks"] = cartons.pop(sdss_id, set())

    return meta
