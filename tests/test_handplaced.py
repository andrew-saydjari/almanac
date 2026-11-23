"""
Hand-placed targets (`almanac/etc/handplaced_targets.yaml`): a retargeted
fiber's row is rewritten with the named object's targeting information
before the cross-match, so the ordinary path assigns its sdss_id.
"""

import math
import textwrap

import h5py as h5
import pytest

from almanac.data_models.exposure import Exposure
from almanac.data_models.plate import PlateTarget
from almanac.data_models.types import RETARGETED_CATEGORY
from almanac.io import write_models_to_hdf5_group
from almanac.overrides import (
    OverrideError,
    apply_exposure_overrides,
    apply_fiber_overrides,
    check_target_names,
    load_handplaced_targets,
    load_overrides,
    overrides_attrs,
    parse_handplaced_targets,
    parse_overrides,
)

TARGETS_TEXT = textwrap.dedent("""
    version: 1
    solar_system_sdss_id: 0
    targets:
      VESTA: {kind: solar_system, jpl_horizons_id: "4"}
      Arcturus: {kind: star, ra: 213.9153003, dec: 19.1824092, twomass_designation: "14153968+1910558"}
      NoTwoMass: {kind: star, ra: 258.66, dec: 14.39, catalogid: 63050400612470882}
""")


def _entry(**kw):
    e = dict(id="t", observatory="apo", mjd=55702, exposures="2", action="retarget_fibers",
             fibers={2: "Arcturus"}, other_fibers="offtarget", reason="r",
             source=dict(file="raw/APOGEE/apo/55702/55702.comments", quote="0009 Arcturus, Fiber 208"))
    e.update(kw)
    return e


def _doc(*entries):
    import yaml
    return yaml.safe_dump({"version": 1, "overrides": list(entries)})


def _plate_row(fiber_id, category="science"):
    return PlateTarget(
        tmass_id=f"2M{fiber_id:08d}-0000000" if category == "science" else "",
        targettype=category, observatory="apo", holeType="apogee", holetype="apogee",
        ra=271.5, dec=-25.0, fiberId=fiber_id,
    )


def _night(n=4):
    """Plate 4925 object exposures 1..n, fibers 1-3 science, fiber 4 sky; no sdss_ids yet."""
    out = []
    for i in range(1, n + 1):
        e = Exposure(observatory="apo", mjd=55702, exposure=i, imagetyp="Object", plateid=4925)
        e._targets = tuple(_plate_row(f, "sky_apogee" if f == 4 else "science") for f in (1, 2, 3, 4))
        out.append(e)
    return out


class TestTargetsFile:
    def test_packaged_file(self):
        t = load_handplaced_targets()
        assert t.solar_system_sdss_id == 0
        assert t.targets["VESTA"].kind == "solar_system"
        for name, x in t.targets.items():
            if x.kind == "star":
                assert x.twomass_designation or x.catalogid > 0, name
                assert 0 <= x.ra < 360 and -90 <= x.dec <= 90
        # Every name used by a shipped retarget entry is in the file (checked on load).
        entries = load_overrides()
        names = {n for e in entries for n in e.fibers.values()}
        assert names <= set(t.targets) and "VESTA" in names and "HD 196944" in names

    def test_attrs_record_targets_file(self):
        assert len(overrides_attrs(True)["handplaced_targets_sha256"]) == 64
        assert "handplaced_targets_sha256" not in overrides_attrs(False)

    @pytest.mark.parametrize("change, message", [
        ("kind: planet", "kind must be"),
        ("ra: abc", "numeric ra"),
        ("ra: 400.0", "out of range"),
        ("twomass_designation: J1415+19", "must look like"),
        ("twomass_designation: ''", "twomass_designation or a catalogid"),
    ])
    def test_invalid_star(self, change, message):
        base = {"kind": "star", "ra": "213.9", "dec": "19.18", "twomass_designation": "14153968+1910558"}
        k, v = change.split(": ", 1)
        base[k] = v
        body = ", ".join(f"{a}: {b}" for a, b in base.items())
        text = f"version: 1\nsolar_system_sdss_id: 0\ntargets:\n  X: {{{body}}}\n"
        with pytest.raises(OverrideError, match=message):
            parse_handplaced_targets(text)

    def test_invalid_file(self):
        for text in ("version: 1\ntargets: {}\n",
                     "version: 2\nsolar_system_sdss_id: 0\ntargets: {X: {kind: solar_system, jpl_horizons_id: '4'}}\n",
                     "version: 1\nsolar_system_sdss_id: x\ntargets: {X: {kind: solar_system, jpl_horizons_id: '4'}}\n",
                     "version: 1\nsolar_system_sdss_id: 0\ntargets: {X: {kind: solar_system}}\n"):
            with pytest.raises(OverrideError):
                parse_handplaced_targets(text)

    def test_unknown_name_fails_loudly(self):
        targets = parse_handplaced_targets(TARGETS_TEXT)
        entries = parse_overrides(_doc(_entry(fibers={2: "Betelgeuse"})))
        with pytest.raises(OverrideError, match="not a target"):
            check_target_names(entries, targets)
        exposures = _night()
        apply_exposure_overrides(exposures, entries)
        with pytest.raises(OverrideError, match="not a target"):
            apply_fiber_overrides(exposures, entries, targets)


class TestRowRewrite:
    def test_star_solar_and_catalogid_rows(self):
        targets = parse_handplaced_targets(TARGETS_TEXT)
        entries = parse_overrides(_doc(
            _entry(id="arc", exposures="2"),
            _entry(id="ves", exposures="3", fibers={1: "VESTA"}),
            _entry(id="cat", exposures="4", fibers={3: "NoTwoMass"}, other_fibers="keep"),
        ))
        exposures = _night()
        apply_exposure_overrides(exposures, entries)
        apply_fiber_overrides(exposures, entries, targets)

        star = {t.fiber_id: t for t in exposures[1].targets}[2]
        assert star.category == "science" and star.original_category == "science"
        assert star.twomass_designation == "14153968+1910558" and star.target_ids == ""
        assert (star.ra, star.dec) == (213.9153003, 19.1824092)
        assert star.sdss_id == -1 and star.catalogid == -1
        assert star.override_id == "arc" and star.override_target == "Arcturus"

        solar = {t.fiber_id: t for t in exposures[2].targets}[1]
        assert solar.category == RETARGETED_CATEGORY and solar.sdss_id == 0
        assert solar.twomass_designation == "" and math.isnan(solar.ra) and math.isnan(solar.dec)

        cat = {t.fiber_id: t for t in exposures[3].targets}[3]
        assert cat.category == "science" and cat.catalogid == 63050400612470882 and cat.twomass_designation == ""
        # other_fibers: keep leaves the rest of that table unchanged.
        assert {t.fiber_id: t for t in exposures[3].targets}[1].category == "science"
        # The plain table (exposure 1) is untouched.
        assert [t.category for t in exposures[0].targets] == ["science"] * 3 + ["sky_apogee"]
        assert [t.twomass_designation for t in exposures[0].targets][1] == "2M00000002-0000000"


class TestCrossMatchThroughNormalPath:
    """get_almanac_data: the edited rows go through the ordinary sdss_id lookups."""

    def _run(self, monkeypatch, entries, overrides=True):
        import sys
        import types
        import almanac.apogee as apogee
        import almanac.overrides as ov

        # A stand-in for almanac.database, so that no connection is attempted.
        fake_db = types.ModuleType("almanac.database")
        fake_db.is_database_available = True
        fake_db.catalogdb = None
        monkeypatch.setitem(sys.modules, "almanac.database", fake_db)

        targets = parse_handplaced_targets(TARGETS_TEXT)
        monkeypatch.setattr(apogee, "get_exposures_and_missing", lambda o, m: (_night(), []))
        monkeypatch.setattr(ov, "overrides_for", lambda o, m: entries)
        monkeypatch.setattr(ov, "load_handplaced_targets", lambda text=None: targets)
        seen = {}

        def fake_apogee_id_lookup(identifiers):
            seen.setdefault("apogee_id", set()).update(identifiers)
            return {"00000001-0000000": 11, "00000002-0000000": 12, "00000003-0000000": 13}

        def fake_twomass(identifiers):
            seen.setdefault("twomass_psc", set()).update(identifiers)
            return {"14153968+1910558": 126102647}

        def fake_catalogid(catalogids):
            seen.setdefault("catalogid", set()).update(catalogids)
            return {63050400612470882: 125919743}

        monkeypatch.setattr(apogee, "get_apogee_id_lookup", fake_apogee_id_lookup)
        monkeypatch.setattr(apogee, "twomass_sdss_id_lookup", fake_twomass)
        monkeypatch.setattr(apogee, "catalogid_sdss_id_lookup", fake_catalogid)
        *_, exposures, _, _ = apogee.get_almanac_data("apo", 55702, fibers=True, meta=True, overrides=overrides)
        return exposures, seen

    def test_retargeted_star_gets_sdss_id(self, monkeypatch):
        entries = parse_overrides(_doc(
            _entry(id="arc", exposures="1-2"),
            _entry(id="ves", exposures="3", fibers={1: "VESTA"}),
            _entry(id="cat", exposures="4", fibers={3: "NoTwoMass"}, other_fibers="keep"),
        ))
        exposures, seen = self._run(monkeypatch, entries)
        by = {e.exposure: e for e in exposures}

        # Exposures 1-2 (the first of the sequence among them) share one edited table.
        assert by[1].fiber_map_key == by[2].fiber_map_key == "4925.override.arc"
        star = {t.fiber_id: t for t in by[1].targets}
        assert star[2].sdss_id == 126102647 and star[2].category == "science"
        assert star[1].category == "offtarget" and star[1].sdss_id == -1
        assert star[4].category == "sky_apogee"
        assert "14153968+1910558" in seen["twomass_psc"]  # same lookup as any plate fiber

        # The plain table (now carried by the first unaffected exposure) is cross-matched as before.
        assert by[3].fiber_map_key != "4925"
        plain = [e for e in exposures if e.fiber_map_key == "4925"]
        assert not plain  # every exposure of this mock night is overridden
        ves = {t.fiber_id: t for t in by[3].targets}
        assert ves[1].sdss_id == 0 and ves[1].category == RETARGETED_CATEGORY

        cat = {t.fiber_id: t for t in by[4].targets}
        assert cat[3].sdss_id == 125919743 and 63050400612470882 in seen["catalogid"]
        assert cat[1].sdss_id == 11 and cat[2].sdss_id == 12  # other_fibers: keep

    def test_plain_table_still_cross_matched(self, monkeypatch):
        entries = parse_overrides(_doc(_entry(id="arc", exposures="1")))
        exposures, _ = self._run(monkeypatch, entries)
        by = {e.exposure: e for e in exposures}
        assert by[1].fiber_map_key == "4925.override.arc"
        assert by[2].fiber_map_key == "4925"
        assert [t.sdss_id for t in by[2].targets] == [11, 12, 13, -1]
        assert {t.fiber_id: t for t in by[1].targets}[2].sdss_id == 126102647

    def test_no_overrides_unchanged(self, monkeypatch):
        entries = parse_overrides(_doc(_entry(id="arc", exposures="1")))
        exposures, seen = self._run(monkeypatch, entries, overrides=False)
        assert all(e.fiber_map_key == "4925" for e in exposures)
        assert [t.sdss_id for t in exposures[0].targets] == [11, 12, 13, -1]
        assert "14153968+1910558" not in seen.get("twomass_psc", set())


def test_metadata_collects_retargeted_sdss_id(tmp_path, monkeypatch):
    """`almanac add metadata` collects sdss_ids from every fibers/* table, override tables included."""
    from almanac.metadata import collect_sdss_ids

    targets = parse_handplaced_targets(TARGETS_TEXT)
    entries = parse_overrides(_doc(_entry(id="arc", exposures="2"), _entry(id="ves", exposures="3", fibers={1: "VESTA"})))
    exposures = _night()
    apply_exposure_overrides(exposures, entries)
    apply_fiber_overrides(exposures, entries, targets)
    {t.fiber_id: t for t in exposures[1].targets}[2].sdss_id = 126102647  # as the cross-match would
    path = tmp_path / "a.h5"
    with h5.File(path, "w") as fp:
        g = fp.create_group("raw/apo/55702/fibers")
        for e in exposures[:3]:
            if e.fiber_map_key not in g:
                write_models_to_hdf5_group(e.targets, g.create_group(e.fiber_map_key))
    ids = collect_sdss_ids(str(path), ["apo"])
    assert 126102647 in ids and 0 in ids


# ---------------------------------------------------------------------------
# The generated handplaced-2011 block (scripts/handplaced_targets/).
# ---------------------------------------------------------------------------

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _make_overrides():
    spec = importlib.util.spec_from_file_location(
        "make_overrides", _ROOT / "scripts" / "handplaced_targets" / "make_overrides.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handplaced_entries():
    return [e for e in load_overrides() if "-handplaced-" in e.id]


class TestHandplaced2011:
    def test_generated_block_is_current(self):
        gen = _make_overrides()
        block = gen.render_block(gen.group_entries(gen.read_rows()))
        text = (_ROOT / "src" / "almanac" / "etc" / "exposure_overrides.yaml").read_text()
        assert gen.current_block(text) == block, "regenerate with make_overrides.py --write-yaml"

    def test_shipped_entries(self):
        entries = _handplaced_entries()
        retarget = {(e.mjd, x) for e in entries if e.action == "retarget_fibers" for x in e.exposures}
        bad = {(e.mjd, x) for e in entries if e.action == "not_science" for x in e.exposures}
        assert len(retarget) == 186 and len(bad) == 39 and not retarget & bad
        assert {m for m, _ in retarget | bad} == {55699, 55700, 55701, 55702, 55725, 55726, 55727, 55729,
                                                   55730, 55731, 55754, 55757, 55759, 55810, 55811, 55815}
        targets = load_handplaced_targets()
        for e in entries:
            assert e.observatory == "apo" and e.mjd != 55755
            if e.action == "retarget_fibers":
                assert e.other_fibers == "offtarget" and len(e.fibers) == 1
                (name,) = e.fibers.values()
                assert targets.targets[name].kind == "star"
                assert "brightest trace" in e.reason
        # Named cases: data check overrides a log/header disagreement; identity in doubt; no plate id.
        by = {(e.mjd, x): e for e in entries for x in e.exposures}
        assert by[(55754, 28)].fibers == {240: "HD 208527"}
        assert by[(55810, 45)].fibers == {158: "HD 156681"}
        assert by[(55729, 58)].fibers == {52: "HD 175305"}
        assert all(by[(55759, x)].action == "not_science" for x in range(59, 69))
        assert by[(55759, 69)].fibers == {230: "HD 9138"}
        assert (55727, 28) not in by and (55729, 71) not in by

    def test_every_entry_applies_to_a_mock_night(self):
        """Apply each night's entries to mock plate exposures with 300-fiber tables: nothing may fail."""
        targets = load_handplaced_targets()
        entries = _handplaced_entries()
        for mjd in sorted({e.mjd for e in entries}):
            night = [e for e in entries if e.mjd == mjd]
            n = max(x for e in night for x in e.exposures) + 1
            exposures = []
            for i in range(1, n + 1):
                x = Exposure(observatory="apo", mjd=mjd, exposure=i, imagetyp="Object", plateid=4000)
                x._targets = tuple(_plate_row(f, "sky_apogee" if f % 10 == 0 else "science") for f in range(1, 301))
                exposures.append(x)
            apply_exposure_overrides(exposures, night)
            apply_fiber_overrides(exposures, night, targets)
            for e in night:
                for num in e.exposures:
                    x = exposures[num - 1]
                    if e.action == "not_science":
                        assert x.flagged_bad and x.fiber_map_key == "4000"
                        continue
                    (fiber, name), = e.fibers.items()
                    rows = {t.fiber_id: t for t in x.targets}
                    t = targets.targets[name]
                    assert rows[fiber].override_target == name and rows[fiber].category == "science"
                    assert rows[fiber].twomass_designation == t.twomass_designation
                    assert (rows[fiber].ra, rows[fiber].dec) == (t.ra, t.dec)
                    others = [r for f, r in rows.items() if f != fiber]
                    assert all(r.category in ("offtarget", "sky_apogee") for r in others)
