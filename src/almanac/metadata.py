"""
Resumable, incremental build of the ``/meta`` group (``almanac add metadata``).

One row per ``sdss_id``: catalog cross-match ids, Gaia DR3 astrometry and
photometry, 2MASS photometry, and SDSS-V targeting flags.

The database work is split into batches of ``sdss_id``. Each finished batch is
written to its own file in a checkpoint directory (write to a temporary name,
then rename), so a run that is killed, or that loses its database connection
for longer than the retries allow, loses at most the batches that were in
flight. Re-running the same command continues from the finished batches.

The final ``/meta`` group is assembled from the checkpoint and written in one
step:

* with ``output``: a new file is written under a temporary name and renamed
  over ``output``; the input file is only ever opened read-only;
* without ``output`` (in place): the group is written as ``meta__tmp`` and
  then renamed to ``meta`` (the old group is unlinked just before), so a
  reader never sees a half-written ``/meta``.

Side groups written with ``/meta``: ``/meta_not_found/sdss_id`` (queried, no
catalog row) and ``/meta_carton_pks/{sdss_id, carton_pk}`` (every carton the
database returned, one row per pair; ``sdss5_target_flags`` can only encode the
cartons the installed ``sdss_semaphore`` knows, listed in the
``unknown_carton_pks`` attribute otherwise).

With ``since``, rows already present in a previously decorated file (or a
targets file) are copied rather than queried.

The input must contain fiber mappings (an almanac file built with
``--fibers``): if none of the selected nights has a ``fibers`` group the build
stops before writing anything (adapted from upstream sdss/almanac #66), and
nights without one are counted in a warning.

With ``extended`` (``almanac add metadata --extended``), the value-added
columns of ``ExtendedSource`` are queried too and written after the default
columns (adapted from upstream sdss/almanac a922dee). Without it the output
is exactly the default ``Source`` columns. The checkpoint records which mode
it was made in and refuses to resume in the other; with ``since``, rows of a
file that lacks the extended columns are queried again rather than copied with
default values.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import signal
import subprocess
import time
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import h5py as h5
import numpy as np

META_GROUP = "meta"
META_TMP_GROUP = "meta__tmp"
NOT_FOUND_GROUP = "meta_not_found"
CARTON_GROUP = "meta_carton_pks"
CHECKPOINT_FORMAT = 1


class MetadataBuildError(RuntimeError):
    """Raised when the build cannot continue (the checkpoint is kept)."""


class _Terminated(BaseException):
    """Raised in the main process on SIGTERM so that workers are stopped."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"[{_utcnow()}] [add metadata] {msg}", flush=True)


def almanac_provenance() -> Dict[str, str]:
    """Return the almanac version and, when run from a git checkout, the commit."""
    from almanac import __version__

    commit = "UNKNOWN"
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        commit = subprocess.run(
            ["git", "-C", here, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", here, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        if dirty:
            commit += "+dirty"
    except Exception:
        pass
    return {"almanac_version": __version__, "almanac_commit": commit}


def database_provenance() -> Dict[str, str]:
    """Database host/port/name from the almanac configuration (no credentials)."""
    try:
        from almanac import config

        sdssdb = config.sdssdb
        host = getattr(sdssdb, "host", None) or "UNKNOWN"
        port = getattr(sdssdb, "port", None)
        return {
            "database_host": f"{host}:{port}" if port else str(host),
            "database_name": "sdss5db",
        }
    except Exception:
        return {"database_host": "UNKNOWN", "database_name": "sdss5db"}


def read_id_list(path: str) -> np.ndarray:
    """Read a list of sdss_id from ``.npy``, an HDF5 file (``/meta/sdss_id`` or
    ``/sdss_id``), or text (one integer per line, ``#`` comments allowed)."""
    if path.endswith(".npy"):
        ids = np.load(path)
    elif path.endswith((".h5", ".hdf5")):
        with h5.File(path, "r") as fp:
            key = f"{META_GROUP}/sdss_id" if f"{META_GROUP}/sdss_id" in fp else "sdss_id"
            ids = fp[key][:]
    else:
        ids = np.loadtxt(path, dtype=np.int64, comments="#", ndmin=1)
    return np.unique(np.asarray(ids, dtype=np.int64))


def collect_sdss_ids(input_path: str, observatories: Sequence[str], mjds=None) -> np.ndarray:
    """Sorted unique sdss_id in ``raw/<obs>/<mjd>/fibers/*/sdss_id`` (read-only)."""
    parts = []
    with h5.File(input_path, "r") as fp:
        for obs in observatories:
            if f"raw/{obs}" not in fp:
                continue
            obs_mjds = list(fp[f"raw/{obs}"]) if mjds is None else [str(m) for m in mjds]
            for mjd in obs_mjds:
                group = fp.get(f"raw/{obs}/{mjd}/fibers")
                if group is None:
                    continue
                for config in group:
                    if "sdss_id" in group[config]:
                        parts.append(np.asarray(group[config]["sdss_id"][:], dtype=np.int64))
    if not parts:
        return np.zeros(0, dtype=np.int64)
    return np.unique(np.concatenate(parts))


def count_nights_with_fibers(input_path: str, observatories: Sequence[str], mjds=None) -> Tuple[int, int]:
    """
    ``(n_nights, n_with_fibers)`` for the selected nights in ``input_path``
    (read-only): how many ``raw/<obs>/<mjd>`` groups match the selection, and
    how many of them have a ``fibers`` group.
    """
    wanted = None if mjds is None else {int(m) for m in mjds}
    n_nights = n_with = 0
    with h5.File(input_path, "r") as fp:
        for obs in observatories:
            if f"raw/{obs}" not in fp:
                continue
            for mjd in fp[f"raw/{obs}"]:
                if wanted is not None and int(mjd) not in wanted:
                    continue
                n_nights += 1
                if f"raw/{obs}/{mjd}/fibers" in fp:
                    n_with += 1
    return n_nights, n_with


def solar_system_ids(ids) -> np.ndarray:
    """
    The non-positive sdss_id in ``ids`` that stand for a solar-system body placed
    on a fiber by an exposure override (``solar_system_sdss_id`` of
    ``almanac/etc/handplaced_targets.yaml``; 0).
    """
    from almanac.overrides import load_handplaced_targets

    ssid = load_handplaced_targets().solar_system_sdss_id
    ids = np.asarray(ids, dtype=np.int64)
    return np.asarray(sorted({int(ssid)} & set(ids.tolist())), dtype=np.int64)


#: The /meta row of a solar-system body (reflected sunlight). It has no catalogue
#: entry, so every catalogue column keeps its default (-1 / "" / NaN): no
#: identifiers, astrometry, photometry, catalogue stellar parameters or
#: targeting flags. Only these are set. Reproducible from the targets file
#: (solar_system_sdss_id) and this constant; never queried.
SOLAR_SYSTEM_META = {"catalogid": -1, "version_id": -1, "lead": "solar_system"}


def solar_system_rows(ids) -> Dict[int, dict]:
    return {int(i): {"sdss_id": int(i), **SOLAR_SYSTEM_META} for i in ids}


def _ids_digest(ids: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(ids, dtype=np.int64).tobytes()).hexdigest()


def _source_fields(extended: bool = False):
    from almanac.data_models.source import Source, ExtendedSource

    model = ExtendedSource if extended else Source
    return model, {**model.model_fields, **model.model_computed_fields}


def extended_field_names() -> List[str]:
    """Names of the columns ``--extended`` adds (in column order)."""
    from almanac.data_models.source import EXTENDED_COLUMN_GROUPS

    return [name for names in EXTENDED_COLUMN_GROUPS.values() for name in names]


def check_extended_requirements() -> None:
    """Fail early, with an actionable message, if ``--extended`` cannot run."""
    try:
        import healpy  # noqa: F401
    except ImportError as e:
        raise MetadataBuildError(
            "--extended needs the 'healpy' package (a declared dependency of almanac), "
            "which is not installed in this environment. Re-sync the environment from "
            "the almanac checkout, e.g. `uv sync --frozen` (project venv) or "
            "`uv pip install -p <env>/bin/python -e <almanac checkout>`."
        ) from e


# --------------------------------------------------------------------------- #
# Batch -> column arrays (identical dtypes to the original writer)
# --------------------------------------------------------------------------- #


def results_to_columns(results: Dict[int, dict], extended: bool = False):
    """
    Turn the per-sdss_id query results of one batch into column arrays.

    The arrays are produced by the original ``write_models_to_hdf5_group`` path
    (through an in-memory HDF5 file), so the dtypes are exactly those the
    in-place command always wrote.

    :returns: (columns: dict name -> ndarray, descriptions: dict, unknown_carton_pks: list,
              carton pairs: (sdss_id ndarray, carton_pk ndarray), every carton_pk returned by
              the database, including those sdss_semaphore does not know)
    """
    from almanac.io import write_models_to_hdf5_group

    Source, fields = _source_fields(extended)
    sdss_id_list = sorted(results.keys())
    unknown = set()
    if not sdss_id_list:
        return {}, {}, [], (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))

    from sdss_semaphore.targeting import TargetingFlags

    flags = TargetingFlags(np.zeros((len(sdss_id_list), 1)))
    sources = []
    pair_ids, pair_pks = [], []
    for i, sdss_id in enumerate(sdss_id_list):
        data = dict(results[sdss_id])
        for carton_pk in sorted(data.pop("carton_pks", set()) or ()):
            pair_ids.append(sdss_id)
            pair_pks.append(carton_pk)
            try:
                flags.set_bit_by_carton_pk(i, carton_pk)
            except KeyError:
                unknown.add(int(carton_pk))
    for i, sdss_id in enumerate(sdss_id_list):
        data = dict(results[sdss_id])
        data.pop("carton_pks", None)
        data["sdss5_target_flags"] = flags.array[i:i + 1]
        sources.append(Source(**data))

    with h5.File(f"meta_{os.getpid()}_{id(results)}.h5", "w", driver="core", backing_store=False) as mem:
        group = mem.create_group("g")
        write_models_to_hdf5_group(sources, group)
        columns = {k: group[k][()] for k in group.keys()}
        descriptions = {k: group[k].attrs.get("description", "") for k in group.keys()}
    pairs = (np.asarray(pair_ids, dtype=np.int64), np.asarray(pair_pks, dtype=np.int64))
    return columns, descriptions, sorted(unknown), pairs


# --------------------------------------------------------------------------- #
# Checkpoint
# --------------------------------------------------------------------------- #


class Checkpoint:
    """
    A directory of finished batches::

        <dir>/manifest.json        what is being built (id digest, batch size, ...)
        <dir>/ids_to_query.npy     the sorted ids to query (defines the batches)
        <dir>/batch_000123.h5      one per finished batch (columns + queried ids)
        <dir>/progress.json        rewritten after every batch
    """

    def __init__(self, path: str):
        self.path = path

    def batch_path(self, index: int) -> str:
        return os.path.join(self.path, f"batch_{index:06d}.h5")

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.path, "manifest.json")

    def exists(self) -> bool:
        return os.path.exists(self.manifest_path)

    def read_manifest(self) -> dict:
        with open(self.manifest_path) as fp:
            return json.load(fp)

    def _atomic_write_text(self, path: str, text: str) -> None:
        tmp = f"{path}.tmp-{os.getpid()}"
        with open(tmp, "w") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)

    def initialise(self, manifest: dict, ids_to_query: np.ndarray) -> None:
        os.makedirs(self.path, exist_ok=True)
        tmp = os.path.join(self.path, f"ids_to_query.tmp-{os.getpid()}.npy")
        np.save(tmp, ids_to_query)
        os.replace(tmp, os.path.join(self.path, "ids_to_query.npy"))
        self._atomic_write_text(self.manifest_path, json.dumps(manifest, indent=1))

    def update_manifest(self, **kwargs) -> None:
        manifest = self.read_manifest()
        manifest.update(kwargs)
        self._atomic_write_text(self.manifest_path, json.dumps(manifest, indent=1))

    def write_progress(self, progress: dict) -> None:
        self._atomic_write_text(
            os.path.join(self.path, "progress.json"), json.dumps(progress, indent=1)
        )

    def is_done(self, index: int) -> bool:
        path = self.batch_path(index)
        if not os.path.exists(path):
            return False
        try:
            with h5.File(path, "r") as fp:
                return bool(fp.attrs.get("complete", False))
        except Exception:
            return False

    def write_batch(self, index: int, queried_ids, columns, descriptions, unknown, t_start, t_end,
                    pairs=None):
        path = self.batch_path(index)
        tmp = f"{path}.tmp-{os.getpid()}"
        with h5.File(tmp, "w") as fp:
            fp.create_dataset("queried_sdss_id", data=np.asarray(queried_ids, dtype=np.int64))
            if pairs is None:
                pairs = (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
            fp.create_dataset("carton_sdss_id", data=np.asarray(pairs[0], dtype=np.int64))
            fp.create_dataset("carton_pk", data=np.asarray(pairs[1], dtype=np.int64))
            g = fp.create_group("columns", track_order=True)
            for name, values in columns.items():
                ds = g.create_dataset(name, data=values)
                ds.attrs["description"] = descriptions.get(name, "")
            fp.attrs["batch_index"] = index
            fp.attrs["n_queried"] = len(queried_ids)
            fp.attrs["n_found"] = len(columns.get("sdss_id", []))
            fp.attrs["unknown_carton_pks"] = np.asarray(unknown, dtype=np.int64)
            fp.attrs["t_start"] = t_start
            fp.attrs["t_end"] = t_end
            fp.attrs["complete"] = True
        os.replace(tmp, path)

    def read_batch(self, index: int):
        with h5.File(self.batch_path(index), "r") as fp:
            queried = fp["queried_sdss_id"][:]
            columns = {k: fp["columns"][k][()] for k in fp["columns"].keys()}
            descriptions = {k: fp["columns"][k].attrs.get("description", "") for k in fp["columns"].keys()}
            unknown = list(fp.attrs.get("unknown_carton_pks", []))
            t_start = fp.attrs.get("t_start", "")
            t_end = fp.attrs.get("t_end", "")
            pairs = (fp["carton_sdss_id"][:], fp["carton_pk"][:])
        return queried, columns, descriptions, unknown, t_start, t_end, pairs


# --------------------------------------------------------------------------- #
# Running the batches
# --------------------------------------------------------------------------- #


def _default_query_batch(sdss_ids, suffix=""):
    from almanac.catalog import _query_catalog

    return _query_catalog(list(sdss_ids), suffix=suffix)


def _extended_query_batch(sdss_ids, suffix=""):
    from almanac.catalog import _query_catalog

    return _query_catalog(list(sdss_ids), suffix=suffix, extended=True)


def _worker_init():
    """Open the worker's database connection before its first batch, so that
    batch timings measure the queries only."""
    try:
        import almanac.database  # noqa: F401  (connects on import)
    except Exception:
        pass


def _timed_call(query_batch, ids, suffix):
    """Run one batch in a worker; return (start_utc, end_utc, result)."""
    t0 = _utcnow()
    result = query_batch(ids, suffix)
    return t0, _utcnow(), result


def _run_batches(
    batches: List[tuple],
    query_batch: Callable,
    n_workers: int,
    on_done: Callable,
    max_batch_attempts: int,
    retry_backoff: float,
    retry_max_delay: float,
) -> None:
    """
    Run ``query_batch(ids, suffix)`` for every ``(index, ids)``; call
    ``on_done(index, ids, result, t_start, t_end)`` in the main process as each
    finishes. A batch that raises (after the per-query reconnect retries in
    ``almanac.retry``) is retried later with exponential backoff, up to
    ``max_batch_attempts`` times; a crashed worker pool is rebuilt.
    """
    attempts = Counter()
    todo = list(batches)
    round_ = 0
    while todo:
        retry = []
        if n_workers <= 0:
            for index, ids in todo:
                t0 = _utcnow()
                try:
                    result = query_batch(ids, f"_{index}")
                except Exception as e:
                    attempts[index] += 1
                    _log(f"batch {index} failed (attempt {attempts[index]}/{max_batch_attempts}): {e!r}")
                    if attempts[index] >= max_batch_attempts:
                        raise MetadataBuildError(
                            f"batch {index} failed {attempts[index]} times; checkpoint kept, re-run to resume"
                        ) from e
                    retry.append((index, ids))
                    continue
                on_done(index, ids, result, t0, _utcnow())
        else:
            import concurrent.futures as cf
            from concurrent.futures.process import BrokenProcessPool

            executor = cf.ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=(
                    _worker_init
                    if query_batch in (_default_query_batch, _extended_query_batch)
                    else None
                ),
            )
            try:
                futures = {}
                for index, ids in todo:
                    futures[executor.submit(_timed_call, query_batch, ids, f"_{index}")] = (index, ids)
                for future in cf.as_completed(futures):
                    index, ids = futures[future]
                    try:
                        t_start, t_end, result = future.result()
                    except BrokenProcessPool as e:
                        _log(f"worker pool broke while running batch {index}: {e!r}; will rebuild it")
                        retry.append((index, ids))
                        continue
                    except Exception as e:
                        attempts[index] += 1
                        _log(f"batch {index} failed (attempt {attempts[index]}/{max_batch_attempts}): {e!r}")
                        if attempts[index] >= max_batch_attempts:
                            raise MetadataBuildError(
                                f"batch {index} failed {attempts[index]} times; checkpoint kept, re-run to resume"
                            ) from e
                        retry.append((index, ids))
                        continue
                    on_done(index, ids, result, t_start, t_end)
            except BaseException:
                # Stop the workers rather than leave orphaned queries running.
                for proc in list(getattr(executor, "_processes", {}).values()):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        if retry:
            delay = min(retry_backoff * 2 ** round_, retry_max_delay)
            round_ += 1
            _log(f"{len(retry)} batch(es) to retry; waiting {delay:.0f} s")
            time.sleep(delay)
        todo = sorted(retry)


# --------------------------------------------------------------------------- #
# Assembly and the final (atomic) write
# --------------------------------------------------------------------------- #


def _read_previous(since_path: str):
    """Columns of ``/meta``, the not-found ids and the raw carton pairs (if any)
    of a previously decorated file."""
    with h5.File(since_path, "r") as fp:
        if META_GROUP not in fp or "sdss_id" not in fp[META_GROUP]:
            raise MetadataBuildError(f"{since_path} has no /{META_GROUP}/sdss_id")
        g = fp[META_GROUP]
        columns = {k: g[k][()] for k in g.keys()}
        descriptions = {k: g[k].attrs.get("description", "") for k in g.keys()}
        not_found = (
            fp[f"{NOT_FOUND_GROUP}/sdss_id"][:]
            if f"{NOT_FOUND_GROUP}/sdss_id" in fp
            else np.zeros(0, dtype=np.int64)
        )
        if f"{CARTON_GROUP}/sdss_id" in fp:
            pairs = (fp[f"{CARTON_GROUP}/sdss_id"][:], fp[f"{CARTON_GROUP}/carton_pk"][:])
        else:
            pairs = None
    return columns, descriptions, np.asarray(not_found, dtype=np.int64), pairs


def _default_for(dtype, shape_tail=()):
    dtype = np.dtype(dtype)
    if dtype.kind == "f":
        fill = np.nan
    elif dtype.kind in "iu":
        fill = -1 if dtype.kind == "i" else 0
    elif dtype.kind == "S":
        fill = b""
    elif dtype.kind == "b":
        fill = False
    else:
        fill = 0
    return fill


def _concat_columns(parts: List[dict], field_names: List[str]) -> dict:
    """Concatenate column dicts; missing columns are filled with defaults and
    2-D/3-D flag arrays are zero-padded to the widest last axis."""
    parts = [p for p in parts if p and len(p.get("sdss_id", [])) > 0]
    out = {}
    if not parts:
        return out
    names = [n for n in field_names if any(n in p for p in parts)]
    for name in names:
        template = next(p[name] for p in parts if name in p)
        arrays = []
        for p in parts:
            n = len(p["sdss_id"])
            if name in p:
                arrays.append(np.asarray(p[name]))
            else:
                arrays.append(np.full((n, *template.shape[1:]), _default_for(template.dtype), dtype=template.dtype))
        if template.ndim > 1:
            width = max(a.shape[-1] for a in arrays)
            arrays = [
                a if a.shape[-1] == width
                else np.concatenate([a, np.zeros((*a.shape[:-1], width - a.shape[-1]), dtype=a.dtype)], axis=-1)
                for a in arrays
            ]
        out[name] = np.concatenate(arrays, axis=0)
    return out


def _write_meta_group(group: h5.Group, columns: dict, descriptions: dict, attrs: dict, chunk_size=1000):
    n = len(columns["sdss_id"]) if columns else 0
    for name, values in columns.items():
        chunks = None
        if n > chunk_size:
            chunks = (chunk_size, *values.shape[1:])
        ds = group.create_dataset(name, data=values, chunks=chunks)
        ds.attrs["description"] = descriptions.get(name, "")
    for key, value in attrs.items():
        if isinstance(value, (list, tuple)):
            if all(isinstance(v, str) for v in value):
                value = np.array([v.encode() for v in value], dtype=h5.string_dtype())
            else:
                value = np.asarray(value)
        elif value is None:
            value = "None"
        group.attrs[key] = value


def _write_final(input_path, output_path, columns, descriptions, extras, attrs):
    """Write ``/meta`` and the side groups in ``extras`` ({group: {name: array}}) atomically."""
    if output_path:
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        tmp = os.path.join(out_dir, f".{os.path.basename(output_path)}.tmp-{os.getpid()}")
        try:
            with h5.File(tmp, "w") as fp:
                g = fp.create_group(META_GROUP, track_order=True)
                _write_meta_group(g, columns, descriptions, attrs)
                for group_name, datasets in extras.items():
                    eg = fp.create_group(group_name)
                    for name, values in datasets.items():
                        eg.create_dataset(name, data=values)
                fp.attrs["written_by"] = "almanac add metadata --output"
            os.replace(tmp, output_path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return output_path

    # In place: build under temporary names, then swap the links.
    names = [(META_GROUP, META_TMP_GROUP)] + [(g, f"{g}__tmp") for g in extras]
    with h5.File(input_path, "a") as fp:
        for _, tmp in names:
            if tmp in fp:
                del fp[tmp]
        try:
            g = fp.create_group(META_TMP_GROUP, track_order=True)
            _write_meta_group(g, columns, descriptions, attrs)
            for group_name, datasets in extras.items():
                eg = fp.create_group(f"{group_name}__tmp")
                for name, values in datasets.items():
                    eg.create_dataset(name, data=values)
            fp.flush()
        except BaseException:
            for _, tmp in names:
                if tmp in fp:
                    del fp[tmp]
            raise
        for final, tmp in names:
            if final in fp:
                del fp[final]
            fp.move(tmp, final)
    return input_path


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_metadata(
    input_path: str,
    output_path: Optional[str] = None,
    since: Optional[str] = None,
    restrict_to: Optional[str] = None,
    observatories: Sequence[str] = ("apo", "lco"),
    mjds=None,
    query_workers: int = 1,
    batch_size: int = 10_000,
    checkpoint_dir: Optional[str] = None,
    fresh: bool = False,
    keep_checkpoint: bool = False,
    retry_not_found: bool = False,
    max_batch_attempts: int = 8,
    retry_backoff: float = 60.0,
    retry_max_delay: float = 1800.0,
    query_batch: Optional[Callable] = None,
    ids: Optional[Iterable[int]] = None,
    extended: bool = False,
) -> dict:
    """
    Build ``/meta`` for the sdss_id in ``input_path``. See the module docstring.

    :param query_batch: function ``(ids, suffix) -> {sdss_id: row dict}``
        (default: the catalog query). Tests pass a fake.
    :param ids: use these sdss_id instead of collecting them from the input
        (the input is then still the provenance record).
    :param extended: also build the value-added columns (``--extended``).
    :returns: a summary dict (also stored as attributes on ``/meta``).
    """
    if extended:
        check_extended_requirements()
    query_batch = query_batch or (_extended_query_batch if extended else _default_query_batch)
    input_path = os.path.abspath(input_path)
    if output_path:
        output_path = os.path.abspath(output_path)
        if output_path == input_path:
            raise MetadataBuildError("--output must differ from INPUT (omit it to write in place)")
    target = output_path or input_path
    checkpoint_dir = checkpoint_dir or f"{target}.meta-checkpoint"
    ckpt = Checkpoint(os.path.abspath(checkpoint_dir))

    # 1. The sdss_id set (input opened read-only).
    if ids is None:
        # A file built without --fibers has no targets: say so and write
        # nothing, instead of writing an empty /meta.
        n_nights, n_with = count_nights_with_fibers(input_path, observatories, mjds)
        if n_with == 0:
            raise MetadataBuildError(
                f"no fiber mappings in {input_path} for the {n_nights} selected night(s), "
                "so there are no targets to add metadata for. Build the almanac file "
                "with --fibers first. Nothing was written."
            )
        if n_with < n_nights:
            _log(
                f"WARNING: {n_nights - n_with} of {n_nights} selected nights in "
                f"{input_path} have no fiber mappings and are skipped"
            )
        all_ids = collect_sdss_ids(input_path, observatories, mjds)
    else:
        all_ids = np.unique(np.asarray(list(ids), dtype=np.int64))
    solar_ids = solar_system_ids(all_ids)
    n_nonpositive = int(np.count_nonzero(all_ids <= 0))
    all_ids = all_ids[all_ids > 0]
    n_input = int(all_ids.size)
    if restrict_to:
        keep = read_id_list(restrict_to)
        all_ids = np.intersect1d(all_ids, keep)
    _log(
        f"{n_input:,} unique positive sdss_id in {input_path}"
        + (f"; {all_ids.size:,} after --restrict-to {restrict_to}" if restrict_to else "")
        + (f" ({n_nonpositive} non-positive value(s) skipped)" if n_nonpositive else "")
        + (f"; fixed solar-system row(s) for sdss_id {solar_ids.tolist()}" if solar_ids.size else "")
    )

    # 2. What is already known (--since).
    Source, fields = _source_fields(extended)
    field_names = list(fields.keys())
    prev_columns, prev_descriptions, prev_not_found = {}, {}, np.zeros(0, dtype=np.int64)
    copied_columns = {}
    copied_pairs = (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    n_copied_without_cartons = 0
    if since:
        prev_columns, prev_descriptions, prev_not_found, prev_pairs = _read_previous(since)
        prev_ids = np.asarray(prev_columns["sdss_id"], dtype=np.int64)
        if extended:
            missing_ext = [n for n in extended_field_names() if n not in prev_columns]
            if missing_ext:
                # Copying these rows would give them default values in the
                # extended columns; query them again instead.
                _log(
                    f"--extended: --since file lacks {len(missing_ext)} extended column(s) "
                    f"(e.g. {missing_ext[0]}); its {prev_ids.size:,} rows are queried again, not copied"
                )
                prev_ids = np.zeros(0, dtype=np.int64)
                prev_columns = {k: v[:0] for k, v in prev_columns.items()}
                prev_pairs = None if prev_pairs is None else (prev_pairs[0][:0], prev_pairs[1][:0])
        mask = np.isin(prev_ids, all_ids)
        copied_columns = {k: v[mask] for k, v in prev_columns.items()}
        if prev_pairs is not None:
            pmask = np.isin(prev_pairs[0], all_ids)
            copied_pairs = (prev_pairs[0][pmask], prev_pairs[1][pmask])
        else:
            n_copied_without_cartons = int(mask.sum())
            _log(f"--since file has no /{CARTON_GROUP}: copied rows keep only sdss5_target_flags")
        known = prev_ids
        if not retry_not_found:
            known = np.union1d(known, prev_not_found)
        ids_to_query = np.setdiff1d(all_ids, known)
        carried_not_found = (
            np.zeros(0, dtype=np.int64) if retry_not_found
            else np.intersect1d(prev_not_found, all_ids)
        )
        extra = sorted(set(prev_columns) - set(field_names))
        if extra:
            _log(f"--since file has columns this almanac does not write (dropped): {extra}")
        _log(
            f"--since {since}: {int(mask.sum()):,} rows copied, "
            f"{carried_not_found.size:,} known not-found ids carried, {ids_to_query.size:,} ids to query"
        )
    else:
        ids_to_query = all_ids
        carried_not_found = np.zeros(0, dtype=np.int64)
    del prev_columns

    # 3. Checkpoint: create, or check that it belongs to this build.
    digest = _ids_digest(ids_to_query)
    if ckpt.exists() and fresh:
        import shutil

        _log(f"--fresh: removing checkpoint {ckpt.path}")
        shutil.rmtree(ckpt.path)
    if ckpt.exists():
        manifest = ckpt.read_manifest()
        if bool(manifest.get("extended", False)) != bool(extended):
            raise MetadataBuildError(
                f"checkpoint {ckpt.path} was made "
                f"{'with' if manifest.get('extended', False) else 'without'} --extended; "
                f"this run is {'with' if extended else 'without'} it. Use --fresh to discard it, "
                "or --checkpoint-dir to use another directory."
            )
        if manifest.get("ids_digest") != digest or manifest.get("batch_size") != batch_size:
            raise MetadataBuildError(
                f"checkpoint {ckpt.path} was made for a different id set or batch size "
                f"({manifest.get('n_ids_to_query')} ids, batch {manifest.get('batch_size')}; "
                f"now {ids_to_query.size} ids, batch {batch_size}). Use --fresh to discard it, "
                "or --checkpoint-dir to use another directory."
            )
        _log(f"resuming from checkpoint {ckpt.path}")
    else:
        ckpt.initialise(
            dict(
                format=CHECKPOINT_FORMAT,
                input_path=input_path,
                output_path=output_path,
                since=os.path.abspath(since) if since else None,
                restrict_to=os.path.abspath(restrict_to) if restrict_to else None,
                n_ids_to_query=int(ids_to_query.size),
                ids_digest=digest,
                batch_size=batch_size,
                **({"extended": True} if extended else {}),
                created_utc=_utcnow(),
                query_start_utc=None,
            ),
            ids_to_query,
        )
        manifest = ckpt.read_manifest()

    batches = [
        (i, ids_to_query[s:s + batch_size].tolist())
        for i, s in enumerate(range(0, ids_to_query.size, batch_size))
    ]
    todo = [(i, b) for i, b in batches if not ckpt.is_done(i)]
    n_done_ids = sum(len(b) for i, b in batches) - sum(len(b) for _, b in todo)
    _log(
        f"{len(batches):,} batches of {batch_size:,}: {len(batches) - len(todo):,} already done, "
        f"{len(todo):,} to run with {query_workers} query worker(s)"
    )
    if todo and not manifest.get("query_start_utc"):
        ckpt.update_manifest(query_start_utc=_utcnow())

    # 4. Run.
    t_run = time.time()
    recent = []
    state = dict(done_ids=n_done_ids, done_batches=len(batches) - len(todo), run_ids=0)
    total_ids = int(ids_to_query.size)

    def on_done(index, ids_b, result, t0, t1):
        columns, descriptions, unknown, pairs = results_to_columns(result, extended)
        ckpt.write_batch(index, ids_b, columns, descriptions, unknown, t0, t1, pairs)
        state["done_ids"] += len(ids_b)
        state["done_batches"] += 1
        state["run_ids"] += len(ids_b)
        now = time.time()
        recent.append((now, len(ids_b)))
        del recent[:-20]
        elapsed = now - t_run
        rate = state["run_ids"] / elapsed if elapsed > 0 else float("nan")
        if len(recent) > 1 and recent[-1][0] > recent[0][0]:
            rate_recent = sum(n for _, n in recent[1:]) / (recent[-1][0] - recent[0][0])
        else:
            rate_recent = rate
        remaining = total_ids - state["done_ids"]
        eta_s = remaining / rate_recent if rate_recent and rate_recent > 0 else float("nan")
        eta = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=eta_s)
        ).isoformat(timespec="minutes") if np.isfinite(eta_s) else "UNKNOWN"
        progress = dict(
            updated_utc=_utcnow(),
            batches_done=state["done_batches"], batches_total=len(batches),
            ids_done=state["done_ids"], ids_total=total_ids,
            rate_ids_per_s_this_run=round(rate, 3), rate_ids_per_s_recent=round(rate_recent, 3),
            eta_utc=eta, last_batch=index, last_batch_found=len(columns.get("sdss_id", [])),
        )
        ckpt.write_progress(progress)
        _log(
            f"batch {index} done ({len(columns.get('sdss_id', [])):,}/{len(ids_b):,} found); "
            f"{state['done_ids']:,}/{total_ids:,} ids ({100 * state['done_ids'] / max(total_ids, 1):.2f}%), "
            f"{rate_recent:.1f} ids/s recent, ETA {eta}"
        )

    def _sigterm(signum, frame):
        raise _Terminated(f"signal {signum}")

    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGTERM, _sigterm)
    except ValueError:  # not in the main thread
        pass
    try:
        _run_batches(todo, query_batch, query_workers, on_done,
                     max_batch_attempts, retry_backoff, retry_max_delay)
    except _Terminated:
        _log("terminated; finished batches are kept in the checkpoint; re-run the same command to resume")
        raise SystemExit(143)
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGTERM, old_handler)
    query_end = _utcnow()
    manifest = ckpt.read_manifest()
    if not manifest.get("query_end_utc") or todo:
        ckpt.update_manifest(query_end_utc=query_end)
        manifest = ckpt.read_manifest()

    # 5. Assemble.
    _log("assembling /meta from the checkpoint")
    parts, descriptions, unknown_all, queried = [copied_columns], dict(prev_descriptions), set(), []
    if solar_ids.size:
        # Fixed rows, never queried (see solar_system_rows).
        solar_columns, solar_descriptions, _, _ = results_to_columns(solar_system_rows(solar_ids), extended)
        parts.append(solar_columns)
        descriptions.update(solar_descriptions)
    pair_parts = [copied_pairs]
    for i, _ in batches:
        q, cols, desc, unknown, _, _, pairs = ckpt.read_batch(i)
        pair_parts.append(pairs)
        queried.append(q)
        parts.append(cols)
        descriptions.update(desc)
        unknown_all.update(int(u) for u in unknown)
    columns = _concat_columns(parts, field_names)
    queried = np.concatenate(queried) if queried else np.zeros(0, dtype=np.int64)
    found = columns.get("sdss_id", np.zeros(0, dtype=np.int64))
    not_found = np.union1d(np.setdiff1d(queried, found), carried_not_found).astype(np.int64)
    if columns:
        order = np.argsort(columns["sdss_id"], kind="stable")
        columns = {k: v[order] for k, v in columns.items()}
        if np.unique(columns["sdss_id"]).size != columns["sdss_id"].size:
            raise MetadataBuildError("duplicate sdss_id in assembled /meta (checkpoint and --since overlap?)")
    carton_ids = np.concatenate([p[0] for p in pair_parts]).astype(np.int64)
    carton_pks = np.concatenate([p[1] for p in pair_parts]).astype(np.int64)
    corder = np.lexsort((carton_pks, carton_ids))
    carton_ids, carton_pks = carton_ids[corder], carton_pks[corder]
    for name in field_names:
        if name in fields and name not in descriptions:
            descriptions[name] = getattr(fields[name], "description", "") or ""

    try:
        import importlib.metadata as im
        semaphore_version = im.version("sdss-semaphore")
    except Exception:
        semaphore_version = "UNKNOWN"

    attrs = dict(
        **almanac_provenance(),
        **database_provenance(),
        input_path=input_path,
        output_path=output_path or input_path,
        mode="output" if output_path else "in_place",
        n_sdss_id=int(found.size),
        n_solar_system_rows=int(solar_ids.size),
        n_sdss_id_input=n_input,
        n_sdss_id_requested=int(all_ids.size),
        n_queried=int(queried.size),
        n_copied_from_since=int(len(copied_columns.get("sdss_id", []))),
        n_not_found=int(not_found.size),
        since_used=bool(since),
        since_path=os.path.abspath(since) if since else "",
        restrict_to=os.path.abspath(restrict_to) if restrict_to else "",
        query_start_utc=manifest.get("query_start_utc") or "",
        query_end_utc=manifest.get("query_end_utc") or "",
        written_utc=_utcnow(),
        batch_size=batch_size,
        query_workers=query_workers,
        sdss_semaphore_version=semaphore_version,
        unknown_carton_pks=np.asarray(sorted(unknown_all), dtype=np.int64),
        n_unknown_carton_pks=len(unknown_all),
        n_carton_pairs=int(carton_ids.size),
        n_copied_without_raw_carton_pks=n_copied_without_cartons,
        columns=list(columns.keys()),
        sdss_id_set_sha256=_ids_digest(all_ids),
    )
    if extended:
        # Only in --extended mode, so that the default /meta keeps its attributes.
        from almanac.data_models.source import EXTENDED_COLUMN_GROUPS

        attrs["extended"] = True
        attrs["extended_column_groups"] = list(EXTENDED_COLUMN_GROUPS.keys())
    extras = {
        NOT_FOUND_GROUP: {"sdss_id": not_found},
        CARTON_GROUP: {"sdss_id": carton_ids, "carton_pk": carton_pks},
    }
    _write_final(input_path, output_path, columns, descriptions, extras, attrs)
    _log(
        f"wrote /{META_GROUP} ({found.size:,} rows; {not_found.size:,} ids not found) to "
        f"{output_path or input_path}"
    )
    if keep_checkpoint:
        _log(f"checkpoint kept at {ckpt.path}")
    else:
        import shutil

        shutil.rmtree(ckpt.path, ignore_errors=True)
    return attrs
