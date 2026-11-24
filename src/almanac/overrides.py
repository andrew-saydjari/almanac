"""
Explicit, version-controlled special cases for individual exposures.

Some exposures cannot be described correctly from their raw headers and the
plugmap / confSummary files alone. The observers' logs say so, for example:

* on APO MJD 55755 the telescope was pointed so that the asteroid Vesta fell
  on single plugged fibers of plate 5099, whose plugmap assigns those fibers
  to bulge stars; every other fiber looked at random sky;
* an FPS exposure whose header names a new configuration while the robots
  were still at the previous one.

Such cases are listed in ``almanac/etc/exposure_overrides.yaml``. Each entry
names an observatory, an MJD, a set of exposures and one action:

``retarget_fibers``
    The listed fibers saw a named object that is not the catalogue target
    (e.g. ``VESTA``, ``HD 196944``). Every name must be a target of
    ``almanac/etc/handplaced_targets.yaml``, which holds its targeting
    information. The fiber's row is rewritten with that information
    *before* the cross-match, so the ordinary almanac path then assigns its
    ``sdss_id`` (and ``almanac add metadata`` later decorates it) exactly as
    for a plugged fiber:

    * a star: category ``science``, the star's 2MASS designation (or SDSS-V
      ``catalogid`` if it has none), ra and dec;
    * a solar-system body (no fixed position, no catalogue entry): category
      ``science`` (so it is reduced), no designation, ra/dec = the raw-header
      RA/DEC of that exposure (each such exposure gets its own table), and
      ``sdss_id`` = ``solar_system_sdss_id`` of the targets file; the
      cross-match skips it (``override_kind`` = ``solar_system``).

    Either way ``override_target`` = the name, ``override_id`` = the entry
    and ``original_category`` = the plugmap category. ``other_fibers`` must
    say what happened to the remaining fibers: ``keep`` (unchanged) or
    ``offtarget`` (the telescope was pointed elsewhere, so no fiber that
    asserts a target identity saw its target; they become ``offtarget`` with
    ``sdss_id`` -1). The affected exposures get their own fiber table, under
    a synthetic ID (:func:`override_table_id`) that replaces their
    ``plate_id`` (FPS: ``config_id``), so that every reader keying on that
    column (ApogeeReduction.jl, ``almanac dump``) opens the edited table; the
    header value is kept in ``original_plate_id`` (``original_config_id``).
``use_config``
    The exposure's fibers were at configuration ``config_id``, not at the one
    in the header.
``use_previous_config``
    The exposure's fibers were still at the configuration of the previous
    object exposure of the night with a different configuration.
``mark_exposure``
    Information only: the exposure is labelled, nothing else changes.
``not_science``
    The exposure is not usable as a science exposure: ``flagged_bad`` is set.

Every entry carries a free-text ``reason`` and a ``source`` (the file and a
verbatim quote). An FPS entry may also record ``recorded_config`` (the
configuration in the header) and ``actual_config`` (the configuration the
fiber positioners were really at). They are optional and must be given
together; when present, the header configuration of every listed exposure
must equal ``recorded_config`` (checked when the entry is applied), and for
``use_config`` ``actual_config`` must equal ``config_id``. They let a
``not_science`` entry be switched to ``use_config`` later without
re-deriving the configurations. Overrides are applied in one place,
:func:`almanac.apogee.get_almanac_data`, and are never silent: affected
exposures carry the entry ids in ``override_ids`` and what was done in
``override_actions``; affected fibers carry ``override_id``. They can be
switched off (``almanac --no-overrides``) so that their effect can be diffed.
A malformed file, an unknown action or key, or an entry that names an
exposure that does not exist all raise :class:`OverrideError`.
"""

import hashlib
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Dict, List, Optional, Tuple

import yaml

#: Category given to a fiber that an override says saw a named object: a star
#: (the row carries the star's targeting information and is cross-matched like
#: any plugged science fiber) or a solar-system body (not cross-matched; its
#: sdss_id is ``solar_system_sdss_id``). Both are reduced as science.
HANDPLACED_STAR_CATEGORY = "science"
HANDPLACED_CATEGORY = HANDPLACED_STAR_CATEGORY

#: Values of the fiber column ``override_kind``.
STAR_KIND = "star"
SOLAR_SYSTEM_KIND = "solar_system"


def skip_cross_match(target) -> bool:
    """True for a fiber whose sdss_id an override set (a solar-system body)."""
    return getattr(target, "override_kind", "") == SOLAR_SYSTEM_KIND

ACTIONS = (
    "retarget_fibers",
    "use_config",
    "use_previous_config",
    "mark_exposure",
    "not_science",
)

_COMMON_REQUIRED = {"id", "observatory", "mjd", "exposures", "action", "reason", "source"}
_ACTION_KEYS = {
    "retarget_fibers": ({"fibers", "other_fibers"}, set()),
    "use_config": ({"config_id"}, set()),
    "use_previous_config": (set(), set()),
    "mark_exposure": (set(), set()),
    "not_science": (set(), set()),
}
_OPTIONAL = {"notes", "recorded_config", "actual_config"}
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_CONFIG_ACTIONS = ("use_config", "use_previous_config")


class OverrideError(ValueError):
    """An override file or entry is malformed, inconsistent, or does not apply."""


@dataclass(frozen=True)
class Override:
    id: str
    observatory: str
    mjd: int
    exposures: Tuple[int, ...]
    action: str
    reason: str
    source_file: str
    source_quote: str
    fibers: Dict[int, str] = field(default_factory=dict)
    other_fibers: str = "keep"
    config_id: int = -1
    notes: str = ""
    recorded_config: int = -1
    actual_config: int = -1


def parse_exposures(value, entry_id="?") -> Tuple[int, ...]:
    """
    Parse an exposure specification: an int, a list of ints, or a string such
    as ``"19-23,29"``. Returns a sorted tuple of unique positive ints.
    """
    items = []
    if isinstance(value, bool):
        raise OverrideError(f"{entry_id}: exposures must be ints, not {value!r}")
    if isinstance(value, int):
        items = [value]
    elif isinstance(value, str):
        for part in value.split(","):
            part = part.strip()
            m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if b < a:
                    raise OverrideError(f"{entry_id}: bad exposure range {part!r}")
                items.extend(range(a, b + 1))
            elif re.fullmatch(r"\d+", part):
                items.append(int(part))
            else:
                raise OverrideError(f"{entry_id}: cannot parse exposures {value!r}")
    elif isinstance(value, (list, tuple)):
        for v in value:
            items.extend(parse_exposures(v, entry_id))
    else:
        raise OverrideError(f"{entry_id}: cannot parse exposures {value!r}")
    if not items or any(i < 1 for i in items):
        raise OverrideError(f"{entry_id}: exposures must be positive, got {value!r}")
    return tuple(sorted(set(items)))


def _validate_entry(raw) -> Override:
    if not isinstance(raw, dict):
        raise OverrideError(f"Override entry must be a mapping, got {raw!r}")
    entry_id = raw.get("id", "?")
    if not isinstance(entry_id, str) or not _ID_PATTERN.match(entry_id):
        raise OverrideError(f"Override id must match {_ID_PATTERN.pattern}, got {entry_id!r}")

    action = raw.get("action")
    if action not in ACTIONS:
        raise OverrideError(f"{entry_id}: unknown action {action!r}; allowed: {', '.join(ACTIONS)}")

    required_extra, optional_extra = _ACTION_KEYS[action]
    required = _COMMON_REQUIRED | required_extra
    allowed = required | optional_extra | _OPTIONAL
    keys = set(raw)
    missing = required - keys
    if missing:
        raise OverrideError(f"{entry_id}: missing key(s) {sorted(missing)}")
    unknown = keys - allowed
    if unknown:
        raise OverrideError(f"{entry_id}: unknown key(s) {sorted(unknown)} for action {action!r}")

    observatory = raw["observatory"]
    if observatory not in ("apo", "lco"):
        raise OverrideError(f"{entry_id}: observatory must be 'apo' or 'lco', got {observatory!r}")
    mjd = raw["mjd"]
    if isinstance(mjd, bool) or not isinstance(mjd, int) or mjd < 50000:
        raise OverrideError(f"{entry_id}: mjd must be an int, got {mjd!r}")
    exposures = parse_exposures(raw["exposures"], entry_id)

    reason = raw["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise OverrideError(f"{entry_id}: reason must be non-empty text")

    source = raw["source"]
    if not isinstance(source, dict) or set(source) != {"file", "quote"}:
        raise OverrideError(f"{entry_id}: source must be a mapping with exactly 'file' and 'quote'")
    for k in ("file", "quote"):
        if not isinstance(source[k], str) or not source[k].strip():
            raise OverrideError(f"{entry_id}: source.{k} must be non-empty text")

    kwds = {}
    if action == "retarget_fibers":
        fibers = raw["fibers"]
        if not isinstance(fibers, dict) or not fibers:
            raise OverrideError(f"{entry_id}: fibers must be a non-empty mapping fiber_id -> target name")
        parsed = {}
        for k, v in fibers.items():
            if isinstance(k, bool) or not isinstance(k, int) or not (1 <= k <= 300):
                raise OverrideError(f"{entry_id}: fiber ids must be ints in 1..300, got {k!r}")
            if not isinstance(v, str) or not v.strip():
                raise OverrideError(f"{entry_id}: fiber {k} needs a target name")
            parsed[k] = v.strip()
        other = raw["other_fibers"]
        if other not in ("keep", "offtarget"):
            raise OverrideError(f"{entry_id}: other_fibers must be 'keep' or 'offtarget', got {other!r}")
        kwds.update(fibers=parsed, other_fibers=other)
    elif action == "use_config":
        cid = raw["config_id"]
        if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
            raise OverrideError(f"{entry_id}: config_id must be a positive int, got {cid!r}")
        kwds.update(config_id=cid)

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise OverrideError(f"{entry_id}: notes must be text")

    configs = [k for k in ("recorded_config", "actual_config") if k in raw]
    if configs:
        if len(configs) != 2:
            raise OverrideError(f"{entry_id}: recorded_config and actual_config must be given together")
        if action == "retarget_fibers":
            raise OverrideError(f"{entry_id}: recorded_config/actual_config are for FPS configuration entries")
        for k in configs:
            v = raw[k]
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise OverrideError(f"{entry_id}: {k} must be a positive int, got {v!r}")
        if raw["recorded_config"] == raw["actual_config"]:
            raise OverrideError(f"{entry_id}: recorded_config and actual_config must differ")
        if action == "use_config" and raw["actual_config"] != kwds["config_id"]:
            raise OverrideError(f"{entry_id}: actual_config must equal config_id for use_config")
        kwds.update(recorded_config=raw["recorded_config"], actual_config=raw["actual_config"])

    return Override(
        id=entry_id,
        observatory=observatory,
        mjd=mjd,
        exposures=exposures,
        action=action,
        reason=reason.strip(),
        source_file=source["file"].strip(),
        source_quote=source["quote"].strip(),
        notes=notes,
        **kwds,
    )


def _check_conflicts(entries: List[Override]) -> None:
    seen_ids = set()
    config_actions = {}
    retargeted = {}
    for e in entries:
        if e.id in seen_ids:
            raise OverrideError(f"Duplicate override id {e.id!r}")
        seen_ids.add(e.id)
        for x in e.exposures:
            key = (e.observatory, e.mjd, x)
            if e.action in _CONFIG_ACTIONS:
                if key in config_actions:
                    raise OverrideError(
                        f"{e.id}: {key} already has a configuration override ({config_actions[key]})"
                    )
                config_actions[key] = e.id
            if e.action == "retarget_fibers":
                for prev_id, prev in retargeted.get(key, []):
                    if prev.other_fibers == "offtarget" or e.other_fibers == "offtarget" or set(prev.fibers) & set(e.fibers):
                        raise OverrideError(
                            f"{e.id}: conflicts with {prev_id} on {key}; "
                            f"combine the fibers into one entry"
                        )
                retargeted.setdefault(key, []).append((e.id, e))


def parse_overrides(text: str) -> List[Override]:
    """Parse and validate the YAML text of an override file."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise OverrideError(f"Override file is not valid YAML: {e}") from e
    if not isinstance(doc, dict) or set(doc) != {"version", "overrides"}:
        raise OverrideError("Override file must be a mapping with exactly 'version' and 'overrides'")
    if doc["version"] != 1:
        raise OverrideError(f"Unsupported override file version {doc['version']!r}")
    raw_entries = doc["overrides"] or []
    if not isinstance(raw_entries, list):
        raise OverrideError("'overrides' must be a list")
    entries = [_validate_entry(raw) for raw in raw_entries]
    _check_conflicts(entries)
    return entries


def _default_text() -> str:
    return (resources.files("almanac.etc") / "exposure_overrides.yaml").read_text()


# ---------------------------------------------------------------------------
# Hand-placed targets (almanac/etc/handplaced_targets.yaml)
# ---------------------------------------------------------------------------

_TARGET_KINDS = ("star", "solar_system")


@dataclass(frozen=True)
class HandplacedTarget:
    """Targeting information that replaces a retargeted fiber's plugmap row."""
    name: str
    kind: str
    ra: float = float("nan")
    dec: float = float("nan")
    twomass_designation: str = ""
    catalogid: int = -1


@dataclass(frozen=True)
class HandplacedTargets:
    targets: Dict[str, HandplacedTarget]
    solar_system_sdss_id: int


def parse_handplaced_targets(text: str) -> HandplacedTargets:
    """Parse and validate the YAML text of a hand-placed targets file."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise OverrideError(f"Hand-placed targets file is not valid YAML: {e}") from e
    if not isinstance(doc, dict) or set(doc) != {"version", "solar_system_sdss_id", "targets"}:
        raise OverrideError(
            "Hand-placed targets file must be a mapping with exactly "
            "'version', 'solar_system_sdss_id' and 'targets'"
        )
    if doc["version"] != 1:
        raise OverrideError(f"Unsupported hand-placed targets file version {doc['version']!r}")
    ssid = doc["solar_system_sdss_id"]
    if isinstance(ssid, bool) or not isinstance(ssid, int):
        raise OverrideError(f"solar_system_sdss_id must be an int, got {ssid!r}")
    raw = doc["targets"]
    if not isinstance(raw, dict) or not raw:
        raise OverrideError("'targets' must be a non-empty mapping name -> target")
    out = {}
    for name, t in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(t, dict):
            raise OverrideError(f"Hand-placed target {name!r} must be a mapping under a non-empty name")
        kind = t.get("kind")
        if kind not in _TARGET_KINDS:
            raise OverrideError(f"Hand-placed target {name!r}: kind must be one of {_TARGET_KINDS}, got {kind!r}")
        if kind == "solar_system":
            if not str(t.get("jpl_horizons_id", "")).strip():
                raise OverrideError(f"Hand-placed target {name!r}: a solar_system target needs jpl_horizons_id")
            out[name] = HandplacedTarget(name=name, kind=kind)
            continue
        try:
            ra, dec = float(t["ra"]), float(t["dec"])
        except (KeyError, TypeError, ValueError):
            raise OverrideError(f"Hand-placed target {name!r}: a star needs numeric ra and dec")
        if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
            raise OverrideError(f"Hand-placed target {name!r}: ra/dec out of range ({ra}, {dec})")
        designation = str(t.get("twomass_designation") or "").strip()
        if designation and not re.fullmatch(r"\d{8}[+-]\d{7}", designation):
            raise OverrideError(
                f"Hand-placed target {name!r}: twomass_designation must look like "
                f"'14153968+1910558', got {designation!r}"
            )
        catalogid = t.get("catalogid", -1)
        if isinstance(catalogid, bool) or not isinstance(catalogid, int):
            raise OverrideError(f"Hand-placed target {name!r}: catalogid must be an int")
        if not designation and catalogid <= 0:
            raise OverrideError(f"Hand-placed target {name!r}: a star needs a twomass_designation or a catalogid")
        out[name] = HandplacedTarget(
            name=name, kind=kind, ra=ra, dec=dec,
            twomass_designation=designation, catalogid=catalogid if catalogid > 0 else -1,
        )
    return HandplacedTargets(targets=out, solar_system_sdss_id=ssid)


def _default_targets_text() -> str:
    return (resources.files("almanac.etc") / "handplaced_targets.yaml").read_text()


_targets_cache: Optional[HandplacedTargets] = None


def load_handplaced_targets(text: Optional[str] = None) -> HandplacedTargets:
    """Load (and cache) the packaged hand-placed targets file, or parse ``text``."""
    global _targets_cache
    if text is not None:
        return parse_handplaced_targets(text)
    if _targets_cache is None:
        _targets_cache = parse_handplaced_targets(_default_targets_text())
    return _targets_cache


def check_target_names(entries: List[Override], targets: HandplacedTargets) -> None:
    """Every ``retarget_fibers`` name must be a hand-placed target."""
    for e in entries:
        for fiber, name in e.fibers.items():
            if name not in targets.targets:
                raise OverrideError(
                    f"{e.id}: fiber {fiber} names {name!r}, which is not a target of "
                    f"handplaced_targets.yaml"
                )


_cache: Optional[Tuple[str, List[Override]]] = None


def load_overrides(text: Optional[str] = None) -> List[Override]:
    """Load (and cache) the packaged override file, or parse ``text``."""
    global _cache
    if text is not None:
        return parse_overrides(text)
    if _cache is None:
        t = _default_text()
        entries = parse_overrides(t)
        check_target_names(entries, load_handplaced_targets())
        _cache = (t, entries)
    return _cache[1]


def overrides_sha256() -> str:
    """SHA-256 of the packaged override file (recorded in the output)."""
    return hashlib.sha256(_default_text().encode()).hexdigest()


def handplaced_targets_sha256() -> str:
    """SHA-256 of the packaged hand-placed targets file (recorded in the output)."""
    return hashlib.sha256(_default_targets_text().encode()).hexdigest()


def overrides_attrs(enabled: bool) -> Dict[str, str]:
    """Per-night HDF5 attributes that record whether overrides were applied."""
    attrs = {"exposure_overrides": "enabled" if enabled else "disabled"}
    if enabled:
        attrs["exposure_overrides_sha256"] = overrides_sha256()
        attrs["handplaced_targets_sha256"] = handplaced_targets_sha256()
    return attrs


def overrides_for(observatory: str, mjd: int, entries: Optional[List[Override]] = None) -> List[Override]:
    entries = load_overrides() if entries is None else entries
    return [e for e in entries if e.observatory == observatory and e.mjd == int(mjd)]


def _set_targets(exposure, targets) -> None:
    """Replace an exposure's targets, dropping the cached `targets` property."""
    exposure._targets = targets
    exposure.__dict__.pop("targets", None)


def _base_key(exposure) -> str:
    return str(exposure.config_id if exposure.fps else exposure.plate_id)


def _header_key(exposure) -> str:
    """Plate / configuration key before any synthetic fiber-table ID (see override_table_id)."""
    return str(exposure.original_config_id if exposure.fps else exposure.original_plate_id)


def assign_override_table_ids(exposures, entries: List[Override],
                              targets: Optional["HandplacedTargets"] = None) -> None:
    """
    Give every exposure edited by ``retarget_fibers`` entries its own fiber
    table ID (:func:`override_table_id`) in ``plate_id`` (plates) or
    ``config_id`` (FPS), keeping the header value in ``original_plate_id``
    (``original_config_id``). Exposures with the same header plate /
    configuration and the same entries share one ID, except that an exposure
    with a solar-system body gets its own (its row holds that exposure's
    position). Their targets are loaded first, under the header ID.
    Idempotent.
    """
    if targets is None:
        try:
            targets = load_handplaced_targets()
        except Exception:
            targets = None
    kinds = {} if targets is None else {n: t.kind for n, t in targets.targets.items()}
    retargets = {}
    solar = set()
    for entry in entries:
        if entry.action != "retarget_fibers":
            continue
        moving = any(kinds.get(name) == SOLAR_SYSTEM_KIND for name in entry.fibers.values())
        for e in _exposures_by_number(exposures, entry):
            retargets.setdefault(e.exposure, (e, []))[1].append(entry.id)
            if moving:
                solar.add(e.exposure)
    todo = [(n, e, ids) for n, (e, ids) in sorted(retargets.items()) if e._override_table_id is None]
    if not todo:
        return
    table_ids = {}
    for e in exposures:  # IDs already given on this night (normally none)
        if e._override_table_id is not None:
            table_ids[e._override_table_group] = e._override_table_id
    for number, e, ids in todo:
        e.targets  # load (and cache) the plate/configuration targets under the header ID
        group = (_header_key(e), tuple(ids), number if number in solar else None)
        if group not in table_ids:
            table_ids[group] = override_table_id(e.mjd, len(set(table_ids.values())) + 1)
        e._override_table_id = table_ids[group]
        e._override_table_group = group
        if e.fps:
            if e._original_config_id is None:
                e._original_config_id = e.config_id
            e.config_id = table_ids[group]
        else:
            e._original_plate_id = e.plate_id
            e.plate_id = table_ids[group]


def _note(exposure, entry: Override, action_text: str) -> None:
    ids = [i for i in exposure.override_ids.split(";") if i]
    acts = [a for a in exposure.override_actions.split(";") if a]
    ids.append(entry.id)
    acts.append(action_text)
    exposure.override_ids = ";".join(ids)
    exposure.override_actions = ";".join(acts)


def _exposures_by_number(exposures, entry: Override):
    by_number = {e.exposure: e for e in exposures if e.image_type != "missing"}
    absent = [x for x in entry.exposures if x not in by_number]
    if absent:
        raise OverrideError(
            f"{entry.id}: exposure(s) {absent} not found on {entry.observatory}/{entry.mjd}"
        )
    return [by_number[x] for x in entry.exposures]


def _config_ids_from_summary(exposure) -> Dict[str, int]:
    """design_id / field_id from the header of the (new) confSummary file, if present."""
    try:
        from pydl.pydlutils.yanny import yanny
        par = yanny(exposure.config_summary_path)
    except Exception:
        return {}
    out = {}
    for key in ("design_id", "field_id"):
        try:
            out[key] = int(par[key])
        except Exception:
            pass
    return out


def apply_exposure_overrides(exposures, entries: List[Override]) -> None:
    """
    Apply the exposure-level actions (everything except the fiber edits of
    ``retarget_fibers``) in place. Must run before sequences are built,
    because a configuration override changes ``config_id``.
    """
    for entry in entries:
        targets = _exposures_by_number(exposures, entry)
        if entry.recorded_config > 0:
            for e in targets:
                if not e.fps or e.config_id != entry.recorded_config:
                    raise OverrideError(
                        f"{entry.id}: exposure {e.exposure} has configuration {e.config_id} "
                        f"(fps={e.fps}), not recorded_config {entry.recorded_config}"
                    )
        if entry.action == "mark_exposure":
            for e in targets:
                _note(e, entry, "mark_exposure")
        elif entry.action == "not_science":
            for e in targets:
                e._override_not_science = True
                _note(e, entry, "not_science")
        elif entry.action == "retarget_fibers":
            for e in targets:
                if e.image_type != "object":
                    raise OverrideError(f"{entry.id}: exposure {e.exposure} is {e.image_type}, not object")
                fibers = ",".join(f"{k}={v}" for k, v in sorted(entry.fibers.items()))
                _note(e, entry, f"retarget_fibers({fibers};others={entry.other_fibers})")
        elif entry.action in _CONFIG_ACTIONS:
            for e in targets:
                if e.image_type != "object" or not e.fps:
                    raise OverrideError(
                        f"{entry.id}: exposure {e.exposure} must be an FPS object exposure for {entry.action}"
                    )
                header_config = e.config_id
                if entry.action == "use_config":
                    new_config = entry.config_id
                else:
                    earlier = [
                        p for p in exposures
                        if p.exposure < e.exposure and p.image_type == "object"
                        and p.config_id > 0 and p.config_id != header_config
                    ]
                    if not earlier:
                        raise OverrideError(
                            f"{entry.id}: no earlier object exposure with another configuration "
                            f"before exposure {e.exposure}"
                        )
                    new_config = earlier[-1].config_id
                if e._original_config_id is None:
                    e._original_config_id = header_config
                e.config_id = new_config
                _set_targets(e, None)
                for k, v in _config_ids_from_summary(e).items():
                    setattr(e, k, v)
                _note(e, entry, f"{entry.action}({header_config}->{new_config})")
    # After the configuration overrides (a retarget's table copies the
    # configuration in force), before sequences are built.
    assign_override_table_ids(exposures, entries)


#: Fiber tables edited by an override are stored under a synthetic ID, which
#: also replaces the exposure's `plate_id` (plates) or `config_id` (FPS): readers
#: that key on that column -- ApogeeReduction.jl's `get_fibTargDict`, `almanac
#: dump stars`/`spectra` -- then open the edited table without any change. The
#: header value is kept in `original_plate_id` / `original_config_id`. IDs are
#: OVERRIDE_TABLE_ID_BASE + (mjd - 50000) * 1000 + k (k = 1, 2, ... per night, in
#: exposure order): positive (no code treats them as "missing"), far above any
#: real plate (< 20,000) or configuration (< 1,000,000) ID, and readable.
OVERRIDE_TABLE_ID_BASE = 1_000_000_000


def override_table_id(mjd: int, k: int) -> int:
    if not 1 <= k <= 999:
        raise OverrideError(f"too many overridden fiber tables on MJD {mjd} ({k})")
    return OVERRIDE_TABLE_ID_BASE + (int(mjd) - 50000) * 1000 + k


def is_override_table_id(value: int) -> bool:
    return int(value) >= OVERRIDE_TABLE_ID_BASE


def header_pointing(exposure):
    """
    (RA, DEC) [deg] from the raw header of an exposure (NaN if unavailable).
    For a hand-placed object this is the object's position at that exposure.
    """
    cached = getattr(exposure, "_header_pointing", None)
    if cached is not None:
        return cached
    radec = (float("nan"), float("nan"))
    try:
        import os
        from astropy.io import fits
        for path in exposure.paths:
            if os.path.exists(path):
                h = fits.getheader(path, 1)
                radec = (float(h["RA"]), float(h["DEC"]))
                break
    except Exception:
        pass
    exposure._header_pointing = radec
    return radec


def _retarget_row(t, entry: Override, target: HandplacedTarget, solar_system_sdss_id: int,
                  pointing=(float("nan"), float("nan"))) -> None:
    """Replace a fiber row's targeting information with a hand-placed target's."""
    t.original_category = t.category
    t.category = HANDPLACED_CATEGORY
    t.override_id = entry.id
    t.override_target = target.name
    t.target_ids = ""
    if target.kind == "star":
        t.override_kind = STAR_KIND
        t.twomass_id = target.twomass_designation
        t.catalogid = target.catalogid
        t.ra, t.dec = target.ra, target.dec
        t.sdss_id = -1  # assigned by the ordinary cross-match
    else:
        t.override_kind = SOLAR_SYSTEM_KIND
        t.twomass_id = ""
        t.catalogid = -1
        t.ra, t.dec = pointing
        t.sdss_id = solar_system_sdss_id


def apply_fiber_overrides(exposures, entries: List[Override], targets: Optional[HandplacedTargets] = None) -> None:
    """
    Apply the fiber edits of ``retarget_fibers`` entries in place. Must run
    BEFORE ``sdss_id`` assignment: each affected exposure gets a copy of the
    fiber table that would otherwise be written for its plate/configuration,
    with the retargeted rows rewritten from ``handplaced_targets.yaml``
    (``targets``; default: the packaged file). Exposures with the same
    plate/configuration and the same entries share one edited table, stored
    under a synthetic ID (:func:`override_table_id`) that replaces their
    ``plate_id`` (or ``config_id``); the header value is kept in
    ``original_plate_id`` (``original_config_id``). The cross-match that
    follows then treats the edited rows like any other (see
    :func:`almanac.apogee.get_almanac_data`), and so does every reader that
    keys on ``plate_id`` / ``config_id``.
    """
    entries = [e for e in entries if e.action == "retarget_fibers"]
    if not entries:
        return
    targets = load_handplaced_targets() if targets is None else targets
    check_target_names(entries, targets)
    assign_override_table_ids(exposures, entries, targets)  # no-op if apply_exposure_overrides did it

    # The table io would write for each header plate/configuration: that of
    # the first exposure with that key and any targets. The targets of the
    # retargeted exposures were loaded under their header IDs.
    reference = {}
    for e in exposures:
        if e.image_type != "object":
            continue
        key = _header_key(e)
        if key not in reference and e.targets:
            reference[key] = e.targets

    affected = {}
    for entry in entries:
        for e in _exposures_by_number(exposures, entry):
            affected.setdefault(e.exposure, (e, []))[1].append(entry)

    for number, (e, es) in sorted(affected.items()):
        key = _header_key(e)
        if key not in reference:
            raise OverrideError(f"{es[0].id}: no fiber table for {key} on exposure {number}")
        new = [t.model_copy(deep=True) for t in reference[key]]
        by_fiber = {t.fiber_id: t for t in new}
        retarget = {}
        for entry in es:
            for fiber, name in entry.fibers.items():
                if fiber not in by_fiber:
                    raise OverrideError(f"{entry.id}: fiber {fiber} not in the fiber table of exposure {number}")
                retarget[fiber] = (entry, name)
        offtarget_entry = next((x for x in es if x.other_fibers == "offtarget"), None)

        for t in new:
            if t.fiber_id in retarget:
                entry, name = retarget[t.fiber_id]
                target = targets.targets[name]
                pointing = header_pointing(e) if target.kind == SOLAR_SYSTEM_KIND else (float("nan"),) * 2
                _retarget_row(t, entry, target, targets.solar_system_sdss_id, pointing)
            elif offtarget_entry is not None:
                from almanac.data_models.types import asserts_target_identity, OFF_TARGET_CATEGORY
                if asserts_target_identity(t.category):
                    t.original_category = t.category
                    t.category = OFF_TARGET_CATEGORY
                    t.sdss_id = -1
                    t.override_id = offtarget_entry.id

        _set_targets(e, tuple(sorted(new, key=lambda t: t.fiber_id)))

    # Make sure the unmodified table for each key is still written (and
    # cross-matched) from the reference target list, even when the first
    # exposure of a sequence is one of the overridden exposures.
    for key, reference_targets in reference.items():
        for e in exposures:
            if e.image_type == "object" and _header_key(e) == key and e.exposure not in affected:
                _set_targets(e, reference_targets)
                break


def rewind_exposure_overrides(exposures) -> None:
    """
    Return exposures read back from an almanac file (`almanac.io.read_exposures`)
    to their header values, undoing what `apply_exposure_overrides` and
    `assign_override_table_ids` stored, so that the night can be processed
    again by exactly the code `almanac` runs (`almanac.apogee.process_night`):

    * `plate_id` / `config_id` go back to `original_plate_id` /
      `original_config_id` (synthetic fiber-table IDs and configuration
      overrides are undone);
    * `override_ids` / `override_actions` are cleared, and so is the private
      override state (not-science flag, fiber-table IDs, cached targets).

    `design_id` / `field_id` are left as stored: only a configuration override
    changes them, and re-applying it sets them again from the confSummary.
    Whether the re-processed exposures equal the stored ones is checked by the
    caller (`almanac add fibers` refuses a night where they differ).
    """
    for e in exposures:
        if e._original_plate_id is not None:
            e.plate_id = e._original_plate_id
        if e._original_config_id is not None:
            e.config_id = e._original_config_id
        e._original_plate_id = None
        e._original_config_id = None
        e._override_table_id = None
        e._override_table_group = None
        e._override_not_science = False
        e.override_ids = ""
        e.override_actions = ""
        _set_targets(e, None)
