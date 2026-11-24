import os
import numpy as np
from glob import glob
from subprocess import check_output
from astropy.table import Table, hstack, unique
from itertools import groupby
from typing import Optional, Tuple, Dict, List, Set, Generator, Any, Union, Sequence

from scipy.spatial.distance import cdist

from almanac import config, logger, utils
from almanac.data_models import Exposure
from almanac.data_models.types import ImageType, assigns_sdss_id
from almanac.data_models.utils import mjd_to_exposure_prefix, get_exposure_path


# Full APOGEE ID -> SDSS ID lookup (every DR17 allStar apogee_id), installed
# only when explicitly requested (`almanac --full-apogee-id-lookup`, via
# `set_apogee_id_lookup`). When it is None, lookups are targeted: only the
# identifiers a night needs are queried, and the answers are memoised in
# `_TARGETED_APOGEE_ID_LOOKUP` for the life of the process.
APOGEE_ID_LOOKUP = None

# Process-local memo for targeted lookups: identifier -> sdss_id for every
# identifier that resolved, and the set of identifiers already queried
# (hits and misses) so that a repeated identifier is never re-queried.
_TARGETED_APOGEE_ID_LOOKUP: Dict[str, int] = {}
_TARGETED_APOGEE_ID_QUERIED: Set[str] = set()

# Prefixes stripped from an allStar apogee_id to form an extra lookup key.
APOGEE_ID_PREFIXES = ("2M", "J", "AP")

# Number of identifiers per targeted query (each identifier expands to
# 1 + len(APOGEE_ID_PREFIXES) candidate apogee_id values in the IN list).
APOGEE_ID_QUERY_BATCH_SIZE = 1000


def _execute_query(q) -> List[Tuple]:
    """
    Execute a peewee query and return all rows, retrying (with reconnect and
    backoff) on transient database connection errors.
    """
    from almanac.retry import retry_on_database_error

    @retry_on_database_error
    def _fetch():
        return list(q)

    return _fetch()


def set_apogee_id_lookup(lookup: Optional[Dict[str, int]]) -> None:
    """
    Install a pre-built, FULL APOGEE ID -> SDSS ID lookup for this process.

    The full lookup is built by `create_apogee_id_lookup` from a heavy
    database query over every DR17 allStar apogee_id (~734k rows; 11-40 min
    warm, hours with a cold database cache). It is only used when explicitly
    requested (`almanac --full-apogee-id-lookup`): the parent builds it once
    and installs it in each pool worker via the pool initializer. When no
    full lookup is installed, `get_apogee_id_lookup` queries only the
    identifiers that are needed.
    """
    global APOGEE_ID_LOOKUP
    APOGEE_ID_LOOKUP = lookup


def clear_targeted_apogee_id_lookup() -> None:
    """Forget all memoised targeted APOGEE ID lookups in this process."""
    _TARGETED_APOGEE_ID_LOOKUP.clear()
    _TARGETED_APOGEE_ID_QUERIED.clear()


def any_plate_era(tasks) -> bool:
    """
    Whether any (mjd, observatory) task is from the plate era (i.e. could
    need the APOGEE ID lookup for target cross-matching).
    """
    from almanac.data_models.exposure import FPS_ERA_START_MJD

    return any(mjd < FPS_ERA_START_MJD[observatory] for mjd, observatory in tasks)


def _add_apogee_id_rows(lookup: Dict[str, int], rows) -> Dict[str, int]:
    """
    Add (sdss_id, apogee_id[, rank]) rows to an APOGEE ID -> SDSS ID lookup.

    Selection rule (see `best_ranked_lookup`): the best (lowest)
    `sdss_id_flat.rank` wins; only among rows with that same best rank does
    the smaller sdss_id win (AKS 2026-11-18: the sdss_id tie-break applies
    "only if there are multiple rank 1 entries"; AKS 2026-11-19: take the
    lowest sdss_id, the convention of other SDSS pipelines). All queries keep
    only rank-1 rows, so in practice the tie-break only separates two or more
    rank-1 sdss_id for one key: `sdss_id_flat.rank` orders the sdss_ids of
    one catalogid, and one star can have two catalogids (e.g. one per allStar
    field) with a rank-1 sdss_id each (64 allStar apogee_ids as of
    2026-11-18). Rows without a rank (the maintainers' lookup table, which
    holds rank-1 pairs only) count as rank 1. The result does not depend on
    row order.

    Each apogee_id is stored as-is, and also with a leading "2M", "J" or "AP"
    stripped, so that a 2MASS-style designation with its prefix removed (see
    `parse_target_identifier`) finds it.
    """
    def _keyed():
        for row in rows:
            sdss_id, apogee_id = row[0], row[1]
            rank = row[2] if len(row) > 2 else 1
            if len(apogee_id.strip()) <= 2:
                # There is one problematic entry of "AP" which caused 180,000
                # fiber-exposures to be assigned to sdss_id 57991461
                continue
            yield apogee_id, sdss_id, rank
            for prefix in APOGEE_ID_PREFIXES:
                if apogee_id.startswith(prefix):
                    yield apogee_id[len(prefix):], sdss_id, rank

    lookup.update(best_ranked_lookup(_keyed()))
    return lookup


def best_ranked_lookup(rows) -> Dict[Any, int]:
    """
    {identifier: sdss_id} from (identifier, sdss_id, rank) rows.

    For each identifier: the row with the best (lowest) `sdss_id_flat.rank`
    wins; if several rows share that best rank, the SMALLEST sdss_id among
    them wins (the convention of other SDSS pipelines). A smaller sdss_id
    never beats a better-ranked one. The result
    is independent of row order. A missing rank (None) counts as worse than
    any rank.
    """
    best: Dict[Any, Tuple[float, int]] = {}
    for identifier, sdss_id, rank in rows:
        r = float("inf") if rank is None else rank
        current = best.get(identifier)
        if (
            current is None
            or r < current[0]
            or (r == current[0] and sdss_id < current[1])
        ):
            best[identifier] = (r, sdss_id)
    return {identifier: sdss_id for identifier, (_, sdss_id) in best.items()}


def apogee_id_candidates(identifier: str) -> Tuple[str, ...]:
    """
    Every allStar apogee_id that can produce the lookup key `identifier`
    under `_add_apogee_id_rows`: the identifier itself, and the identifier
    with each stripped prefix put back.
    """
    return (identifier, ) + tuple(f"{prefix}{identifier}" for prefix in APOGEE_ID_PREFIXES)


def _query_apogee_id_rows(apogee_ids: Optional[Sequence[str]] = None) -> List[Tuple[int, str, int]]:
    """
    Query (sdss_id, apogee_id, rank) rows linking DR17 allStar entries to
    SDSS IDs (`sdss_id_flat.rank = 1` only, as before; the rank is returned
    so that the fold in `_add_apogee_id_rows` can apply "best rank first"
    explicitly rather than relying on this filter).

    :param apogee_ids:
        If given, only rows whose allStar apogee_id is in this collection are
        returned (the join is then driven from the small allStar side through
        its apogee_id index, so the large sdss_id tables are only probed by
        index for those rows). If None, every allStar entry is returned (the
        full lookup).
    """
    from almanac.database import catalogdb

    from peewee import (ForeignKeyField, BooleanField, FloatField, TextField)

    class CatalogToAllStar_DR17_synspec_rev1(catalogdb.CatalogdbModel):
        catalog = ForeignKeyField(catalogdb.Catalog,
                                column_name='catalogid',
                                backref='+')
        target = ForeignKeyField(catalogdb.AllStar_DR17_synspec_rev1,
                                column_name='target_id',
                                backref='+')
        version = ForeignKeyField(catalogdb.Version,
                                column_name='version_id',
                                backref='+')
        best = BooleanField()
        distance = FloatField()
        plan_id = TextField(null=True)

        class Meta:
            table_name = "catalog_to_allstar_dr17_synspec_rev1"
            primary_key = False
            use_reflection = False

    if apogee_ids is None:
        q = (
            catalogdb.SDSS_ID_flat
            .select(
                catalogdb.SDSS_ID_flat.sdss_id,
                catalogdb.AllStar_DR17_synspec_rev1.apogee_id,
                catalogdb.SDSS_ID_flat.rank,
            )
            .join(
                catalogdb.SDSS_ID_To_Catalog,
                on=(
                    catalogdb.SDSS_ID_flat.catalogid
                    == catalogdb.SDSS_ID_To_Catalog.catalogid
                )
            )
            .join(
                CatalogToAllStar_DR17_synspec_rev1,
                on=(
                    catalogdb.SDSS_ID_To_Catalog.catalogid
                    == CatalogToAllStar_DR17_synspec_rev1.catalogid
                ),
            )
            .join(
                catalogdb.AllStar_DR17_synspec_rev1,
                on=(
                    CatalogToAllStar_DR17_synspec_rev1.target_id
                    == catalogdb.AllStar_DR17_synspec_rev1.apstar_id
                ),
            )
            .where(catalogdb.SDSS_ID_flat.rank == 1)
            .tuples()
        )
    else:
        # Same joins and filter as the full query, written from the allStar
        # side and restricted to the requested apogee_id values.
        q = (
            catalogdb.AllStar_DR17_synspec_rev1
            .select(
                catalogdb.SDSS_ID_flat.sdss_id,
                catalogdb.AllStar_DR17_synspec_rev1.apogee_id,
                catalogdb.SDSS_ID_flat.rank,
            )
            .join(
                CatalogToAllStar_DR17_synspec_rev1,
                on=(
                    CatalogToAllStar_DR17_synspec_rev1.target_id
                    == catalogdb.AllStar_DR17_synspec_rev1.apstar_id
                ),
            )
            .join(
                catalogdb.SDSS_ID_To_Catalog,
                on=(
                    CatalogToAllStar_DR17_synspec_rev1.catalogid
                    == catalogdb.SDSS_ID_To_Catalog.catalogid
                ),
            )
            .join(
                catalogdb.SDSS_ID_flat,
                on=(
                    catalogdb.SDSS_ID_To_Catalog.catalogid
                    == catalogdb.SDSS_ID_flat.catalogid
                ),
            )
            .where(
                catalogdb.AllStar_DR17_synspec_rev1.apogee_id.in_(list(apogee_ids))
            &   (catalogdb.SDSS_ID_flat.rank == 1)
            )
            .tuples()
        )
    return _execute_query(q)


# ---------------------------------------------------------------------------
# Lookup source: an optional materialized view, else the live join.
#
# If the database has a materialized view of (apogee_id, sdss_id) pairs (name
# from `config.apogee_id_lookup_view`; default
# "catalogdb.mv_apogee_id_to_sdss_id"), almanac reads it instead of running
# the four-table join. The view is a snapshot: it is only correct if it was
# refreshed after the last update of sdss_id_flat, sdss_id_to_catalog,
# catalog_to_allstar_dr17_synspec_rev1 and allstar_dr17_synspec_rev1. The
# source is resolved once per process (`resolve_apogee_id_source`), its
# comment ("Last refreshed: YYYY-MM-DD") is logged, a heuristic staleness
# check is run (WARNING only), and the chosen source is recorded in the
# output (see `apogee_id_source_attrs`). If the view is absent, unreadable or
# empty, the live join is used and nothing fails.
#
# The maintainers delivered `catalogdb.mv_apogee_id_to_sdss_id` on 2026-11-18
# as an ordinary table (not a materialized view; sdssdb model
# `catalogdb.MvApogeeIdToSdssId`, sdssdb main e7d71cb). Either kind is read
# the same way; "view" below means whichever relation is configured.
# ---------------------------------------------------------------------------

# Source tables the view is a snapshot of.
APOGEE_ID_VIEW_SOURCE_TABLES = (
    "catalogdb.sdss_id_flat",
    "catalogdb.sdss_id_to_catalog",
    "catalogdb.catalog_to_allstar_dr17_synspec_rev1",
    "catalogdb.allstar_dr17_synspec_rev1",
)

SOURCE_VIEW = "materialized_view"
SOURCE_LIVE_JOIN = "live_join"

# Resolved once per process (or installed by the parent via
# `set_apogee_id_source`); see `resolve_apogee_id_source`.
_APOGEE_ID_SOURCE: Optional[Dict[str, Any]] = None
_FORCE_LIVE_APOGEE_ID_JOIN = False

_VIEW_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$"


def force_live_apogee_id_join(force: bool = True) -> None:
    """
    Always use the live join (never the materialized view) in this process.
    Clears any already-resolved source.
    """
    global _FORCE_LIVE_APOGEE_ID_JOIN, _APOGEE_ID_SOURCE
    _FORCE_LIVE_APOGEE_ID_JOIN = bool(force)
    _APOGEE_ID_SOURCE = None


def set_apogee_id_source(source: Optional[Dict[str, Any]]) -> None:
    """Install a source already resolved by the parent (pool workers)."""
    global _APOGEE_ID_SOURCE
    _APOGEE_ID_SOURCE = source


def _quote_view_name(name: str) -> str:
    import re

    if not re.match(_VIEW_NAME_PATTERN, name):
        raise ValueError(f"Invalid apogee_id_lookup_view name: {name!r}")
    return ".".join(f'"{part}"' for part in name.split("."))


def _execute_sql(sql: str, params=None) -> List[Tuple]:
    """Run raw SQL and return all rows, with the same retry as `_execute_query`."""
    from almanac.retry import retry_on_database_error
    from almanac.database import database

    @retry_on_database_error
    def _fetch():
        return list(database.execute_sql(sql, params).fetchall())

    return _fetch()


def parse_view_refresh_date(comment: Optional[str]):
    """The date in a "Last refreshed: YYYY-MM-DD" comment, or None."""
    import re
    from datetime import date

    if not comment:
        return None
    m = re.search(r"Last refreshed:\s*(\d{4})-(\d{2})-(\d{2})", comment, re.IGNORECASE)
    if not m:
        return None
    try:
        return date(*map(int, m.groups()))
    except ValueError:
        return None


def check_view_staleness(refreshed, table_stats) -> List[str]:
    """
    Client-side staleness check of the lookup table / materialized view.
    Silent unless there is positive evidence of a problem.

    :param refreshed:
        The view's last-refresh `date` (from a "Last refreshed: YYYY-MM-DD"
        comment), or None.
    :param table_stats:
        Rows of (table, last_analyze, last_autoanalyze, n_mod_since_analyze)
        from `pg_stat_all_tables` for the source tables.

    :returns:
        Warning messages (empty if no sign of staleness).

    The only warning: a source table was (auto)analyzed on a day AFTER the
    refresh date, i.e. it was very likely changed after the snapshot was
    built. Nothing is reported when the date is unknown (no comment; AKS
    2026-11-18: "just remove the warning"), when statistics are missing, or
    for unanalyzed modifications (`n_mod_since_analyze`), since none of
    those is evidence that the snapshot is out of date. The direct evidence,
    an identifier the table misses but the live join matches, is still
    logged as a WARNING by `_targeted_batch`.
    """
    warnings = []
    if refreshed is None:
        return warnings
    for table, last_analyze, last_autoanalyze, n_mod in table_stats:
        stamps = [t for t in (last_analyze, last_autoanalyze) if t is not None]
        if not stamps:
            continue
        latest = max(stamps)
        latest_date = latest.date() if hasattr(latest, "date") else latest
        if latest_date > refreshed:
            warnings.append(
                f"{table} was analyzed on {latest_date}, after the view's "
                f"last refresh ({refreshed}): it has probably changed since"
            )
    return warnings


# pg_class.relkind -> name, for logging what kind of relation the lookup is.
RELKIND_NAMES = {
    "r": "table",
    "m": "materialized view",
    "v": "view",
    "p": "partitioned table",
    "f": "foreign table",
}


def _probe_view(view: str) -> Dict[str, Any]:
    """
    Read-only catalog checks for the view. Returns a dict with keys
    exists, readable, nonempty, kind, comment, refreshed, table_stats.

    `kind` is what the relation is ("table", "materialized view", "view",
    ...; "" if absent). The database maintainers delivered
    `catalogdb.mv_apogee_id_to_sdss_id` (2026-11-18) as an ordinary table
    despite its `mv_` prefix; any relation with `apogee_id` and `sdss_id`
    columns works, and the kind is only logged and recorded.
    """
    quoted = _quote_view_name(view)
    info = dict(exists=False, readable=False, nonempty=False, kind="", comment=None,
                refreshed=None, table_stats=[])
    ((regclass, ), ) = _execute_sql("SELECT to_regclass(%s)::text", (view, ))
    if regclass is None:
        return info
    info["exists"] = True
    ((readable, comment, relkind), ) = _execute_sql(
        "SELECT has_table_privilege(to_regclass(%s), 'SELECT'), "
        "obj_description(to_regclass(%s), 'pg_class'), "
        "(SELECT relkind::text FROM pg_class WHERE oid = to_regclass(%s))",
        (view, view, view)
    )
    info["kind"] = RELKIND_NAMES.get(relkind, relkind or "")
    info["readable"] = bool(readable)
    info["comment"] = comment
    info["refreshed"] = parse_view_refresh_date(comment)
    if not readable:
        return info
    info["nonempty"] = bool(_execute_sql(f"SELECT 1 FROM {quoted} LIMIT 1"))
    schemas_tables = [t.split(".", 1) for t in APOGEE_ID_VIEW_SOURCE_TABLES]
    info["table_stats"] = _execute_sql(
        "SELECT schemaname || '.' || relname, last_analyze, last_autoanalyze, "
        "n_mod_since_analyze FROM pg_stat_all_tables "
        "WHERE (schemaname, relname) IN (" + ", ".join(["(%s, %s)"] * len(schemas_tables)) + ")",
        tuple(x for st in schemas_tables for x in st)
    )
    return info


def resolve_apogee_id_source(force_live_join: Optional[bool] = None) -> Dict[str, Any]:
    """
    Decide (once per process) whether APOGEE ID lookups read the materialized
    view or run the live join, log the decision, and return a description:

        source: "materialized_view" or "live_join"
        view: configured view name ("" if disabled)
        view_comment, view_last_refreshed: from the view's COMMENT
        reason: why the live join is used (if it is)
        staleness_warnings: list of heuristic staleness warnings

    Never raises: any problem probing the view falls back to the live join.
    """
    global _APOGEE_ID_SOURCE
    if force_live_join is not None:
        force_live_apogee_id_join(force_live_join)
    if _APOGEE_ID_SOURCE is not None:
        return _APOGEE_ID_SOURCE

    view = (getattr(config, "apogee_id_lookup_view", "") or "").strip()
    source = dict(source=SOURCE_LIVE_JOIN, view=view, view_kind="", view_comment="",
                  view_last_refreshed="", reason="", staleness_warnings=[])

    if _FORCE_LIVE_APOGEE_ID_JOIN:
        source["reason"] = "live join forced (--live-apogee-id-join)"
    elif not view:
        source["reason"] = "no view configured (apogee_id_lookup_view is empty)"
    else:
        try:
            info = _probe_view(view)
        except Exception as e:
            source["reason"] = f"could not probe view {view}: {e}"
            logger.warning(f"APOGEE ID lookup: {source['reason']}; using the live join")
        else:
            source["view_kind"] = info.get("kind") or ""
            source["view_comment"] = info["comment"] or ""
            source["view_last_refreshed"] = (
                info["refreshed"].isoformat() if info["refreshed"] else ""
            )
            if not info["exists"]:
                source["reason"] = f"view {view} does not exist"
            elif not info["readable"]:
                source["reason"] = f"view {view} is not readable by this account"
                logger.warning(f"APOGEE ID lookup: {source['reason']}; using the live join")
            elif not info["nonempty"]:
                source["reason"] = f"view {view} is empty (never refreshed?)"
                logger.warning(f"APOGEE ID lookup: {source['reason']}; using the live join")
            else:
                source["source"] = SOURCE_VIEW
                logger.info(
                    f"APOGEE ID lookup: reading {view} "
                    f"({source['view_kind'] or 'relation of unknown kind'}; last refreshed: {source['view_last_refreshed'] or 'UNKNOWN'}; "
                    f"comment: {source['view_comment']!r})"
                )
                try:
                    source["staleness_warnings"] = check_view_staleness(
                        info["refreshed"], info["table_stats"]
                    )
                except Exception as e:
                    source["staleness_warnings"] = [f"staleness check failed: {e}"]
                for message in source["staleness_warnings"]:
                    logger.warning(
                        f"APOGEE ID lookup: view {view} may be stale: {message}. "
                        f"The view must be refreshed whenever its source tables "
                        f"are updated; use --live-apogee-id-join to bypass it."
                    )

    if source["source"] == SOURCE_LIVE_JOIN:
        logger.info(f"APOGEE ID lookup: using the live join ({source['reason']})")
    _APOGEE_ID_SOURCE = source
    return source


def apogee_id_source_attrs(source: Optional[Dict[str, Any]], full: bool = False) -> Dict[str, str]:
    """HDF5 attributes recording which APOGEE ID lookup source a night used."""
    if source is None:
        return {}
    return {
        "apogee_id_lookup_source": source["source"],
        "apogee_id_lookup_mode": "full" if full else "targeted",
        "apogee_id_lookup_view": source["view"] if source["source"] == SOURCE_VIEW else "",
        "apogee_id_lookup_view_kind": (
            source.get("view_kind", "") if source["source"] == SOURCE_VIEW else ""
        ),
        "apogee_id_lookup_view_last_refreshed": (
            source["view_last_refreshed"] if source["source"] == SOURCE_VIEW else ""
        ),
        "apogee_id_lookup_view_comment": (
            source["view_comment"] if source["source"] == SOURCE_VIEW else ""
        ),
        "apogee_id_lookup_staleness_warnings": "; ".join(source["staleness_warnings"]),
        "apogee_id_lookup_note": source["reason"],
    }


def prepare_cross_match(
    xmatch: bool,
    live_apogee_id_join: bool = False,
    full_apogee_id_lookup: bool = False,
):
    """
    The once-per-run cross-match setup shared by `almanac --fibers` and
    `almanac add fibers`, run in the parent process before any worker starts.

    Decides whether APOGEE ID lookups read the materialized view or run the
    live join (logging the view's refresh date and any staleness warning),
    and, with `full_apogee_id_lookup`, builds the full lookup once and
    installs it in this process.

    :returns:
        `(source, lookup_attrs, full_lookup)`: the resolved source (to ship to
        pool workers), the per-night attributes that record it, and the full
        lookup or None. All empty/None when `xmatch` is False.
    """
    if not xmatch:
        return None, {}, None
    source = resolve_apogee_id_source(force_live_join=live_apogee_id_join)
    lookup_attrs = apogee_id_source_attrs(source, full=full_apogee_id_lookup)
    full_lookup = None
    if full_apogee_id_lookup:
        # Opt-in: one heavy query for every DR17 allStar APOGEE ID
        # (~734k rows; 11-40 min warm, hours with a cold database
        # cache). By default each night queries only the APOGEE IDs it
        # needs (see `get_apogee_id_lookup`).
        logger.info(
            "Building full APOGEE ID lookup (--full-apogee-id-lookup; "
            "slow database step)"
        )
        full_lookup = create_apogee_id_lookup()
        set_apogee_id_lookup(full_lookup)
    return source, lookup_attrs, full_lookup


def _query_apogee_id_rows_from_view(view: str, apogee_ids: Optional[Sequence[str]] = None) -> List[Tuple[int, str]]:
    """
    (sdss_id, apogee_id) rows from the materialized view: all rows, or only
    those whose apogee_id is in `apogee_ids` (`= ANY(array)`, via its index).

    The relation has no rank column: it holds only rank-1 pairs (the same
    `sdss_id_flat.rank = 1` join; checked row by row against the live join
    for all 657,134 apogee_ids on 2026-11-18). `_add_apogee_id_rows` treats
    rows without a rank as rank 1.
    """
    quoted = _quote_view_name(view)
    if apogee_ids is None:
        return _execute_sql(f"SELECT sdss_id, apogee_id FROM {quoted}")
    return _execute_sql(
        f"SELECT sdss_id, apogee_id FROM {quoted} WHERE apogee_id = ANY(%s)",
        (list(apogee_ids), )
    )


def create_apogee_id_lookup() -> Dict[str, int]:
    """
    Create the FULL lookup dictionary mapping every DR17 allStar APOGEE ID
    (with and without its "2M"/"J"/"AP" prefix) to an SDSS ID.

    Reads the whole materialized view if it is the resolved source (see
    `resolve_apogee_id_source`), else runs the heavy live join (~734k rows;
    see `set_apogee_id_lookup`). Normal runs do not need it:
    `get_apogee_id_lookup` queries only the identifiers a night needs. It is
    kept for `almanac --full-apogee-id-lookup`.
    """
    source = resolve_apogee_id_source()
    if source["source"] == SOURCE_VIEW:
        rows = _query_apogee_id_rows_from_view(source["view"])
    else:
        rows = _query_apogee_id_rows()
    return _add_apogee_id_rows({}, rows)


def _targeted_batch(batch: Sequence[str], source: Dict[str, Any]) -> Dict[str, int]:
    """
    Look up one batch of identifiers; return {identifier: sdss_id} for the
    identifiers that matched (values exactly as the full lookup would hold).

    With the view as source, identifiers that the view does not match are
    looked up again with the live join (the view may be stale; a genuine
    no-match costs one small index-driven query, memoised). Any match the
    live join finds that the view lacked is logged as a WARNING: it is direct
    evidence that the view is out of date.
    """
    def _fold(ids, fetch):
        candidates = sorted({c for identifier in ids for c in apogee_id_candidates(identifier)})
        found = _add_apogee_id_rows({}, fetch(candidates))
        # Keep only the requested keys: every row that can produce a
        # requested key was fetched by this query, so those values are
        # complete. Other keys produced along the way (e.g. the full
        # "2M..." form) may be missing rows and are not stored.
        return {identifier: found[identifier] for identifier in ids if identifier in found}

    if source["source"] != SOURCE_VIEW:
        return _fold(batch, _query_apogee_id_rows)

    result = _fold(batch, lambda c: _query_apogee_id_rows_from_view(source["view"], c))
    missing = [identifier for identifier in batch if identifier not in result]
    if missing:
        recovered = _fold(missing, _query_apogee_id_rows)
        if recovered:
            logger.warning(
                f"APOGEE ID lookup: {len(recovered)} of {len(missing)} identifiers "
                f"not in view {source['view']} were matched by the live join "
                f"(e.g. {sorted(recovered)[:5]}): the view is stale and must be "
                f"refreshed; using the live-join values for them"
            )
        result.update(recovered)
    return result


def get_apogee_id_lookup(
    identifiers,
    batch_size: Optional[int] = None,
) -> Dict[str, int]:
    """
    Return an APOGEE ID -> SDSS ID lookup that contains an entry for every
    identifier in `identifiers` that has a match.

    If a full lookup was installed (`set_apogee_id_lookup`), it is returned
    unchanged. Otherwise only the identifiers not already queried by this
    process are looked up, in batches of `batch_size` identifiers, from the
    materialized view if available (identifiers it misses are retried with
    the live join) or else with the live join, and the answers (hits and
    misses) are memoised for the life of the process.

    For each requested identifier the value is exactly what the full lookup
    built by `create_apogee_id_lookup` would hold for that key: every allStar
    apogee_id that can produce the key (the identifier, or the identifier
    with "2M", "J" or "AP" prepended) is fetched in the same query, and the
    rows are folded with the same rules (bad short entries such as "AP" are
    skipped; best rank first, and among rows of equal best rank the smallest
    sdss_id, as in the full lookup).

    The returned dictionary is the process-wide memo; callers must not
    mutate it. Only requested identifiers are guaranteed to be present.
    """
    if APOGEE_ID_LOOKUP is not None:
        return APOGEE_ID_LOOKUP

    if batch_size is None:
        batch_size = APOGEE_ID_QUERY_BATCH_SIZE

    todo = set(identifiers).difference(_TARGETED_APOGEE_ID_QUERIED)
    # A blank identifier (empty 2MASS designation, common on FPS nights) can
    # never match: all of its candidates are two characters or fewer after
    # stripping, which `_add_apogee_id_rows` always skips. Record it as a
    # miss without a query.
    blank = {identifier for identifier in todo if not identifier.strip()}
    _TARGETED_APOGEE_ID_QUERIED.update(blank)
    todo = sorted(todo - blank)
    if not todo:
        return _TARGETED_APOGEE_ID_LOOKUP

    from time import time
    t_start = time()
    source = resolve_apogee_id_source()
    n_before = len(_TARGETED_APOGEE_ID_LOOKUP)
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        _TARGETED_APOGEE_ID_LOOKUP.update(_targeted_batch(batch, source))
        _TARGETED_APOGEE_ID_QUERIED.update(batch)

    logger.info(
        f"Targeted APOGEE ID lookup ({source['source']}): {len(todo)} identifiers "
        f"queried ({-(-len(todo) // batch_size)} batch(es)), "
        f"{len(_TARGETED_APOGEE_ID_LOOKUP) - n_before} matched, "
        f"in {time() - t_start:.2f} s"
    )
    return _TARGETED_APOGEE_ID_LOOKUP


# ---------------------------------------------------------------------------
# Identifier -> sdss_id lookups for the catalogid, Gaia DR2 and 2MASS paths.
#
# Each query keeps only `sdss_id_flat.rank = 1` rows (as before 2026-11-18)
# and also returns the rank; `best_ranked_lookup` then picks, per
# identifier, the best rank and, only among rows of that equal best rank,
# the smallest sdss_id. With the rank-1 filter, the tie-break only acts when
# an identifier reaches two or more rank-1 sdss_id (e.g. a Gaia DR2 source
# or 2MASS designation cross-matched to two catalogids).
# ---------------------------------------------------------------------------

def catalogid_sdss_id_lookup(catalogids) -> Dict[int, int]:
    """{catalogid: sdss_id} for `catalogids` (rank 1; see above)."""
    from almanac.database import catalogdb

    q = (
        catalogdb.SDSS_ID_flat
        .select(
            catalogdb.SDSS_ID_flat.catalogid,
            catalogdb.SDSS_ID_flat.sdss_id,
            catalogdb.SDSS_ID_flat.rank,
        )
        .where(
            catalogdb.SDSS_ID_flat.catalogid.in_(tuple(catalogids))
        &   (catalogdb.SDSS_ID_flat.rank == 1)
        )
        .tuples()
    )
    return best_ranked_lookup(_execute_query(q))


def gaia_dr2_sdss_id_lookup(source_ids) -> Dict[int, int]:
    """{gaia_dr2_source_id: sdss_id} for `source_ids` (rank 1; see above)."""
    from almanac.database import catalogdb

    q = (
        catalogdb.SDSS_ID_flat
        .select(
            catalogdb.CatalogToGaia_DR2.target,
            catalogdb.SDSS_ID_flat.sdss_id,
            catalogdb.SDSS_ID_flat.rank,
        )
        .join(
            catalogdb.CatalogToGaia_DR2,
            on=(
                catalogdb.SDSS_ID_flat.catalogid == catalogdb.CatalogToGaia_DR2.catalog
            )
        )
        .where(
            catalogdb.CatalogToGaia_DR2.target.in_(tuple(source_ids))
        &   (catalogdb.SDSS_ID_flat.rank == 1)
        )
        .tuples()
    )
    return best_ranked_lookup(_execute_query(q))


def twomass_sdss_id_lookup(designations) -> Dict[str, int]:
    """{2MASS PSC designation: sdss_id} for `designations` (rank 1; see above)."""
    from almanac.database import catalogdb

    q = (
        catalogdb.SDSS_ID_flat
        .select(
            catalogdb.TwoMassPSC.designation,
            catalogdb.SDSS_ID_flat.sdss_id,
            catalogdb.SDSS_ID_flat.rank,
        )
        .join(
            catalogdb.CatalogToTwoMassPSC,
            on=(
                catalogdb.SDSS_ID_flat.catalogid
                == catalogdb.CatalogToTwoMassPSC.catalogid
            ),
        )
        .join(
            catalogdb.TwoMassPSC,
            on=(
                catalogdb.CatalogToTwoMassPSC.target_id
                == catalogdb.TwoMassPSC.pts_key
            ),
        )
        .where(
            catalogdb.TwoMassPSC.designation.in_(tuple(designations))
        &   (catalogdb.SDSS_ID_flat.rank == 1)
        )
        .tuples()
    )
    return best_ranked_lookup(_execute_query(q))



def get_unique_exposure_paths(paths: List[str]) -> List[str]:
    """
    Process a list of file paths to find unique exposures and determine which chips are available.

    :param paths:
        List of file paths to APOGEE exposure files.

    :returns:
        List of exposure paths.
    """

    chip_mapping = {}
    for path in paths:
        _, observatory, mjd, basename = path.rsplit("/", 3)
        prefix, chip, exposure_apz = basename.split("-")

        key = (observatory, mjd, exposure_apz)
        chip_mapping.setdefault(key, [prefix, [False, False, False]])
        index = "abc".index(chip)
        chip_mapping[key][1][index] = True

    unique_exposure_paths = []
    for (observatory, mjd, exposure_apz), (prefix, chips) in chip_mapping.items():
        chip = "abc"[chips.index(True)]
        path = f"{config.apogee_dir}/{observatory}/{mjd}/{prefix}-{chip}-{exposure_apz}"
        unique_exposure_paths.append(path)

    return unique_exposure_paths


def get_exposures(observatory: str, mjd: int) -> Generator[Exposure, None, None]:
    """
    Generate exposures taken from a given observatory on a given MJD.

    :param observatory:
        The observatory name (e.g. "apo").

    :param mjd:
        The Modified Julian Date.

    :yields:
        Exposure instances for each unique exposure found on disk.
    """
    exposures, _ = get_exposures_and_missing(observatory, mjd)
    return exposures


def get_exposures_and_missing(
    observatory: str, mjd: int
) -> Tuple[List[Exposure], List[Dict[str, Any]]]:
    """
    Like `get_exposures`, but also return the missing-exposures report for this
    observatory and MJD (see `classify_missing_exposures`).

    :param observatory:
        The observatory name (e.g. "apo").
    :param mjd:
        The Modified Julian Date.

    :returns:
        A two-tuple of (`exposures`, `missing`), where `missing` is a list of
        missing-exposure records (dicts).
    """
    paths = glob(get_exposure_path(observatory, mjd, "a?R", "*", "*"))
    disk_exposures = list(map(Exposure.from_path, get_unique_exposure_paths(paths)))

    expected_exposure_numbers, db_status = get_expected_exposure_numbers(observatory, mjd)
    n_expected = max(expected_exposure_numbers) if expected_exposure_numbers else -1

    exposures = organize_exposures(disk_exposures, n_expected=n_expected)
    missing = classify_missing_exposures(
        observatory, mjd, exposures, expected_exposure_numbers, db_status
    )
    return exposures, missing


def get_expected_exposure_numbers(
    observatory: str, mjd: int
) -> Tuple[Optional[List[int]], str]:
    """
    Query the SDSS database for the full list of exposure numbers expected for
    a given observatory and MJD. This is used to identify missing exposures,
    and to distinguish between different kinds of missing-ness (see
    `classify_missing_exposures`).

    :param observatory:
        The observatory name (e.g. "apo").
    :param mjd:
        The Modified Julian Date.

    :returns:
        A two-tuple of (`exposure_numbers`, `status`):

        - `exposure_numbers`: sorted list of within-night exposure numbers
          (1-indexed) known to the operations database, or `None` if the
          database was not (or could not be) queried. An empty list means the
          query succeeded but the database has no rows for this night.
        - `status`: one of "ok" (query succeeded), "not_queried" (MJD predates
          database coverage), or "unavailable" (query failed after retries).
    """

    if mjd < int(getattr(config.sdssdb_exposure_min_mjd, observatory)):
        return None, "not_queried"

    from almanac.database import opsdb
    from almanac.retry import retry_on_database_error

    for model in (opsdb.Exposure, opsdb.ExposureFlavor):
        model._meta.schema = f"opsdb_{observatory}"

    start, end = map(mjd_to_exposure_prefix, (mjd, mjd + 1))

    q = (
        opsdb.Exposure.select(
            opsdb.Exposure.exposure_no
        )
        .where(
            (opsdb.Exposure.exposure_no > start)
        &   (opsdb.Exposure.exposure_no < end)
        )
        .join(
            opsdb.ExposureFlavor,
            on=(opsdb.ExposureFlavor.pk == opsdb.Exposure.exposure_flavor),
        )
        .tuples()
    )

    @retry_on_database_error
    def _fetch():
        return sorted(int(exposure_no) - start for (exposure_no, ) in q)

    try:
        return _fetch(), "ok"
    except Exception as e:
        logger.warning(
            f"Could not query expected exposures for {observatory}/{mjd} "
            f"(database unavailable?): {e}"
        )
        return None, "unavailable"


def get_expected_number_of_exposures(observatory: str, mjd: int) -> int:
    """
    Query the SDSS database to get the expected number of exposures for a given
    observatory and MJD. Returns -1 when the database has no answer (predates
    coverage, no rows, or unavailable).

    Kept for backwards compatibility; prefer `get_expected_exposure_numbers`,
    which distinguishes those cases and returns the full exposure number list.
    """
    exposure_numbers, _ = get_expected_exposure_numbers(observatory, mjd)
    return max(exposure_numbers) if exposure_numbers else -1


def organize_exposures(
    exposures: List[Exposure],
    n_expected: Optional[int] = None
) -> List[Exposure]:
    """
    Identify any missing exposures (based on non-contiguous exposure numbers)
    and fill them with missing image types, guaranteeing dense 1..N exposure
    rows.

    :param exposures:
        A list of `Exposure` instances.

    :param n_expected: [optional]
        The maximum within-night exposure number expected from the operations
        database (-1 if unknown). If `None`, the database is queried.

    :returns:
        A list of organized `Exposure` instances.
    """

    exposures = sorted(exposures, key=lambda x: x.exposure)

    if len(exposures) == 0:
        return []

    observatory, mjd = (exposures[0].observatory, exposures[0].mjd)

    if n_expected is None:
        n_expected = get_expected_number_of_exposures(observatory, mjd)
    max_exposure = max(exposures[-1].exposure, n_expected)

    organized = []
    for i in range(1, max_exposure + 1):
        if exposures and exposures[0].exposure == i:
            organized.append(exposures.pop(0))
        else:
            organized.append(
                Exposure(
                    observatory=observatory,
                    exposure=i,
                    mjd=mjd,
                    image_type="missing"
                )
            )
    return organized


# Reason codes for the missing-exposures report:
#   hole           - no file on disk and no database record; interior gap in
#                    the on-disk exposure numbering
#   trailing       - no file on disk and no database record; beyond the last
#                    on-disk exposure but within the database-expected range
#   db_no_file     - the operations database has a record of this exposure,
#                    but no file exists on disk
#   file_no_db     - a file exists on disk, but the operations database has no
#                    record of this exposure
#   db_unavailable - sentinel (exposure = -1) recording that the database
#                    could not be queried for this observatory/MJD, so
#                    trailing missing exposures are undetectable
MISSING_EXPOSURE_REASONS = (
    "hole", "trailing", "db_unavailable", "db_no_file", "file_no_db"
)


def classify_missing_exposures(
    observatory: str,
    mjd: int,
    exposures: List[Exposure],
    expected_exposure_numbers: Optional[List[int]],
    db_status: str,
) -> List[Dict[str, Any]]:
    """
    Build the missing-exposures report for one observatory and MJD by comparing
    the organized exposure list (dense 1..N, holes filled with
    `image_type="missing"`) against the exposure numbers known to the
    operations database.

    :param observatory:
        The observatory name (e.g. "apo").
    :param mjd:
        The Modified Julian Date.
    :param exposures:
        Organized list of `Exposure` instances (from `organize_exposures`).
    :param expected_exposure_numbers:
        Sorted within-night exposure numbers known to the operations database,
        or `None` (see `get_expected_exposure_numbers`).
    :param db_status:
        Status string from `get_expected_exposure_numbers`.

    :returns:
        A list of records (dicts) with keys: observatory, mjd, exposure,
        expected_max_db, reason. Reasons are described in
        `MISSING_EXPOSURE_REASONS`.
    """
    expected_max_db = (
        max(expected_exposure_numbers) if expected_exposure_numbers else -1
    )
    expected_set = set(expected_exposure_numbers or [])
    on_disk = [e.exposure for e in exposures if e.image_type != "missing"]
    max_on_disk = max(on_disk, default=0)

    records = []

    def add(exposure, reason):
        records.append(
            dict(
                observatory=observatory,
                mjd=mjd,
                exposure=exposure,
                expected_max_db=expected_max_db,
                reason=reason,
            )
        )

    if db_status == "unavailable":
        # Only disk-detectable holes can be reported; trailing missing
        # exposures are undetectable. Record a sentinel row so the failure to
        # check is loud rather than silent.
        for e in exposures:
            if e.image_type == "missing":
                add(e.exposure, "hole")
        add(-1, "db_unavailable")
        return records

    for e in exposures:
        if e.image_type == "missing":
            if db_status == "ok" and e.exposure in expected_set:
                add(e.exposure, "db_no_file")
            elif e.exposure < max_on_disk:
                add(e.exposure, "hole")
            else:
                add(e.exposure, "trailing")
        elif db_status == "ok" and e.exposure not in expected_set:
            add(e.exposure, "file_no_db")

    if not exposures and expected_set:
        # The database expects exposures but nothing is on disk at all.
        # (`organize_exposures` returns [] in this case, so record them here.)
        for exposure in sorted(expected_set):
            add(exposure, "db_no_file")

    return records


def get_sequences(
    exposures: List[Exposure],
    image_type: ImageType,
    fields: Tuple[str, ...],
    ignore_interrupting_darks: bool = False
) -> List[Tuple[int, int]]:
    """
    Get exposure number ranges for sequences of a specific image type.

    :param exposures:
        Astropy Table containing exposure metadata.
    :param image_type:
        The image type to search for (e.g., "Object", "ArcLamp").
    :param fields:
        Tuple of column names to group exposures by.
    :param ignore_interrupting_darks: [optional]
        Whether to allow (and ignore) dark exposures interrupting sequences.

    :returns:
        List of tuples containing (start_exposure, end_exposure) for each sequence.
    """
    if ignore_interrupting_darks:
        f = lambda x: x.image_type in (image_type, "dark")
    else:
        f = lambda x: x.image_type == image_type

    s = list(filter(f, exposures))

    sequence_exposure_numbers = []
    for v, group in groupby(s, key=lambda x: tuple(getattr(x, f) for f in fields)):
        for si, ei in utils.group_contiguous([e.exposure for e in group]):
            sequence_exposure_numbers.append((si, ei))
    return sequence_exposure_numbers


def get_arclamp_sequences(exposures: List[Exposure]) -> List[Tuple[int, int]]:
    """
    Return a list of tuples indicating the start and end exposure numbers for
    a sequence of arc lamp exposures.

    :param exposures:
        A list of `Exposure` instances.

    :returns:
        List of tuples containing (start_exposure, end_exposure) for each arc lamp sequence.
    """
    return get_sequences(
        exposures,
        "arclamp",
        ("dithered_pixels", ),
        ignore_interrupting_darks=True
    )


def get_science_sequences(exposures: List[Exposure]) -> List[Tuple[int, int]]:
    """
    Return a list of tuples indicating the start and end exposure numbers for
    a sequence of science exposures.

    :param exposures:
        A list of `Exposure` instances.

    :returns:
        List of tuples containing (start_exposure, end_exposure) for each science sequence.
    """
    return get_sequences(exposures, "object", ("field_id", "plate_id", "config_id", "image_type"))


def parse_target_identifier(target):

    # catalogids prior to 4204682057 don't have a direct sdss_id mapping.
    if target.catalogid >= 4204682057:
        return ("catalogid", target.catalogid)
    else:
        if target.twomass_designation.startswith("GAIA_DR2"):
            return ("gaia_dr2", np.int64(target.twomass_designation.split(" ")[1]))
        else:
            designation = target.twomass_designation
            for prefix in ("2MASS-J", "2MASS", "2M", "J", "APG-J", "AP"):
                if designation.startswith(prefix):
                    designation = designation[len(prefix):]
                    break
            return ("coordinate", designation)


def cross_match_targets(
    exposures: List[Exposure],
    science_sequences,
    xmatch: bool = True,
    only_assign_sdss_id_to_first_exposure_per_configuration: bool = True,
    include_first_exposure_per_fiber_map_key: bool = False,
) -> None:
    """
    Load the fiber-to-target mapping for the first exposure of each science
    sequence and, optionally, cross-match those targets with the SDSS-V
    database to assign each one an `sdss_id`.

    The targets are read from the confSummary (FPS era) or plugmap (plate era)
    files for the exposure. This mutates the given exposures in place.

    The cross-match uses the targeted lookups (`catalogid_sdss_id_lookup`,
    `gaia_dr2_sdss_id_lookup`, `get_apogee_id_lookup`,
    `twomass_sdss_id_lookup`): the best-ranked row wins, ties broken to the
    smallest `sdss_id` (`best_ranked_lookup`); APOGEE IDs are queried per
    night unless a full lookup was installed. Targets whose category does not assign an `sdss_id`
    (`assigns_sdss_id`: unplugged and off-target fibers) keep -1, and
    solar-system bodies placed by an override keep the `sdss_id` set there
    (`overrides.skip_cross_match`).

    :param exposures:
        The exposures for one observatory and MJD, in exposure-number order
        (i.e. `exposures[i].exposure == i + 1`).
    :param science_sequences:
        (start, end) pairs of 1-indexed exposure numbers for each science
        sequence, as returned by `get_science_sequences`.
    :param xmatch:
        Whether to cross-match targets with the database to assign `sdss_id`.
    :param only_assign_sdss_id_to_first_exposure_per_configuration:
        If True (default), only assign `sdss_id` to the targets of the first
        exposure in each science sequence.
    :param include_first_exposure_per_fiber_map_key:
        Also cross-match the first object exposure of every fiber table that
        will be written (`Exposure.fiber_map_key`), so that per-exposure
        tables made by fiber overrides (applied before this call) are
        cross-matched too. `get_almanac_data` sets it when the night has
        overrides.
    """
    # The exposures whose target lists are cross-matched: one per science
    # sequence (all of them, if requested), plus the first exposure of
    # every fiber table that will be written (`fiber_map_key`), so that
    # the edited per-exposure tables are cross-matched too.
    xmatch_indices = []
    for si, ei in science_sequences:
        xmatch_indices.extend(
            [si - 1] if only_assign_sdss_id_to_first_exposure_per_configuration else range(si - 1, ei)
        )
    if include_first_exposure_per_fiber_map_key:
        first_of_key = {}
        for i, e in enumerate(exposures):
            if e.image_type == "object":
                first_of_key.setdefault(e.fiber_map_key, i)
        xmatch_indices.extend(first_of_key.values())
    xmatch_indices = list(dict.fromkeys(xmatch_indices))

    identifiers = {}
    for i in xmatch_indices:
        for target in exposures[i].targets:
            key, identifier = parse_target_identifier(target)
            identifiers.setdefault(key, set()).add(identifier)

    if xmatch:
        # We will often run `get_almanac_data` in parallel (through multiple processes),
        # so here we are avoiding opening a database connection until the child process starts.
        from almanac.database import is_database_available, catalogdb

        lookup = {}
        if "catalogid" in identifiers and is_database_available:
            lookup["catalogid"] = catalogid_sdss_id_lookup(identifiers["catalogid"])

        if "gaia_dr2" in identifiers and is_database_available:
            lookup["gaia_dr2"] = gaia_dr2_sdss_id_lookup(identifiers["gaia_dr2"])

        if "coordinate" in identifiers and is_database_available:
            # Query only the APOGEE IDs this night needs (memoised per
            # process), unless a full lookup was installed.
            lookup["apogee_id"] = get_apogee_id_lookup(identifiers["coordinate"])
            remaining = set(identifiers["coordinate"]).difference(lookup["apogee_id"].keys())

            if remaining:
                # For remaining things, check 2MASS.
                lookup["twomass_psc"] = twomass_sdss_id_lookup(remaining)

        # Add sdss_id to targets
        from almanac.overrides import skip_cross_match
        for i in xmatch_indices:
            exposure = exposures[i]
            for target in exposure.targets:
                if skip_cross_match(target):
                    # A solar-system body placed on the fiber by an
                    # override: its sdss_id was set there.
                    continue
                if not assigns_sdss_id(target.category):
                    # Don't assign sdss_id to unplugged targets, or to
                    # off-target fibers: the positioner never reached
                    # the assigned target, so whatever light the fiber
                    # collected did not come from that source.
                    continue

                key, identifier = parse_target_identifier(target)
                if key == "coordinate":
                    for lookup_key in ("apogee_id", "twomass_psc"):
                        target.sdss_id = lookup[lookup_key].get(identifier, -1)
                        if target.sdss_id != -1:
                            break
                else:
                    target.sdss_id = lookup[key].get(identifier, -1)


def get_almanac_data(
    observatory: str,
    mjd: int,
    fibers: bool = False,
    meta: bool = False,
    only_assign_sdss_id_to_first_exposure_per_configuration: bool = True,
    overrides: bool = True,
):
    """
    Return comprehensive almanac data for all exposures taken from a given observatory on a given MJD.

    :param observatory:
        The observatory name (e.g. "apo").
    :param mjd:
        The Modified Julian Date.
    :param fibers:
        Whether to include fiber mapping information.
    :param meta:
        Whether to cross-match the fiber targets with the catalog database to
        assign `sdss_id` values (ignored unless `fibers` is True).
    :param only_assign_sdss_id_to_first_exposure_per_configuration:
        See `cross_match_targets`.
    :param overrides: [optional]
        Apply the exposure overrides in `almanac/etc/exposure_overrides.yaml`
        (see `almanac.overrides`). They are applied in `process_night`, the
        only place they are applied.

    :returns:
        Tuple containing:
        - observatory name
        - MJD
        - A list of exposures
        - dictionary of sequence indices (including "missing" exposure ranges)
        - list of missing-exposure records (see `classify_missing_exposures`)
    """

    exposures, missing = get_exposures_and_missing(observatory, mjd)
    return process_night(
        observatory,
        mjd,
        exposures,
        missing,
        fibers=fibers,
        meta=meta,
        only_assign_sdss_id_to_first_exposure_per_configuration=(
            only_assign_sdss_id_to_first_exposure_per_configuration
        ),
        overrides=overrides,
    )


def process_night(
    observatory: str,
    mjd: int,
    exposures: List[Exposure],
    missing=(),
    fibers: bool = False,
    meta: bool = False,
    only_assign_sdss_id_to_first_exposure_per_configuration: bool = True,
    overrides: bool = True,
):
    """
    Everything `get_almanac_data` does after the exposures are read: apply the
    exposure overrides, build the sequences and (with `fibers`) apply the
    fiber overrides and cross-match the fiber targets.

    This is the one code path for a night. `almanac` calls it through
    `get_almanac_data` with exposures read from the raw headers; `almanac add
    fibers` calls it with exposures read back from an almanac file and rewound
    to their header values (`almanac.overrides.rewind_exposure_overrides`).

    :param exposures:
        The night's exposures, in exposure-number order, with header values
        (no override applied yet). Modified in place.
    :param missing:
        The missing-exposure records, returned unchanged.

    The other parameters and the return value are those of `get_almanac_data`.
    """
    night_overrides = []
    if overrides:
        from almanac.overrides import overrides_for, apply_exposure_overrides
        night_overrides = overrides_for(observatory, mjd)
        if night_overrides:
            # Before sequences: a configuration override changes `config_id`,
            # and an exposure whose fibers are edited gets its own fiber-table
            # ID in `plate_id` / `config_id` (so it is not part of the plate's
            # or configuration's visit).
            apply_exposure_overrides(exposures, night_overrides)
            logger.info(
                f"{observatory}/{mjd}: applied exposure overrides "
                f"{', '.join(o.id for o in night_overrides)}"
            )

    sequences = {
        "objects": get_science_sequences(exposures),
        "arclamps": get_arclamp_sequences(exposures),
        "missing": get_sequences(exposures, "missing", ()),
    }
    if fibers:
        if night_overrides:
            # BEFORE the cross-match: a retarget rewrites the targeting
            # information of the fiber rows (from handplaced_targets.yaml)
            # in a per-exposure copy of the fiber table, and the cross-match
            # below then treats those rows exactly like plugged fibers.
            from almanac.overrides import apply_fiber_overrides
            apply_fiber_overrides(exposures, night_overrides)

        cross_match_targets(
            exposures,
            sequences["objects"],
            xmatch=meta,
            only_assign_sdss_id_to_first_exposure_per_configuration=(
                only_assign_sdss_id_to_first_exposure_per_configuration
            ),
            include_first_exposure_per_fiber_map_key=bool(night_overrides),
        )

    return (observatory, mjd, exposures, sequences, list(missing))
