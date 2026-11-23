"""Tests for the exposure-override mechanism (`almanac.overrides`)."""

import textwrap

import h5py as h5
import pytest

from almanac.data_models.exposure import Exposure
from almanac.data_models.plate import PlateTarget
from almanac.data_models.types import OFF_TARGET_CATEGORY, RETARGETED_CATEGORY, assigns_sdss_id
from almanac.io import write_models_to_hdf5_group
from almanac.overrides import (
    OverrideError,
    apply_exposure_overrides,
    apply_fiber_overrides,
    load_handplaced_targets,
    load_overrides,
    overrides_attrs,
    overrides_for,
    parse_exposures,
    parse_overrides,
)


def entry(**kwargs):
    e = dict(
        id="apo-55755-test",
        observatory="apo",
        mjd=55755,
        exposures="19-20",
        action="mark_exposure",
        reason="a reason",
        source=dict(file="raw/APOGEE/apo/55755/55755.comments", quote="19 something"),
    )
    e.update(kwargs)
    return e


def doc(*entries):
    import yaml
    return yaml.safe_dump({"version": 1, "overrides": list(entries)})


def plate_target(fiber_id, category="science", sdss_id=1000):
    return PlateTarget(
        sdss_id=sdss_id + fiber_id if category != "sky_apogee" else -1,
        tmass_id=f"2M{fiber_id:08d}-0000000",
        targettype=category,
        observatory="apo",
        holeType="apogee",
        holetype="apogee",
        ra=271.5,
        dec=-25.0,
        fiberId=fiber_id,
    )


TEST_TARGETS = load_handplaced_targets(textwrap.dedent("""
    version: 1
    solar_system_sdss_id: 0
    targets:
      VESTA: {kind: solar_system, jpl_horizons_id: "4"}
      HD 1: {kind: star, ra: 1.25, dec: 67.8, twomass_designation: "00050000+6748000"}
"""))


def make_exposures(n=6, plate=5099):
    """Object exposures 1..n of one plate, each with a 4-fiber table; fiber 4 is sky."""
    exposures = []
    for i in range(1, n + 1):
        e = Exposure(observatory="apo", mjd=55755, exposure=i, imagetyp="Object", plateid=plate)
        # Only the first exposure carries cross-matched sdss_ids, as in get_almanac_data.
        sdss = 1000 if i == 1 else -1000
        e._targets = tuple(
            plate_target(f, "sky_apogee" if f == 4 else "science", sdss_id=sdss) for f in (1, 2, 3, 4)
        )
        exposures.append(e)
    return exposures


class TestParsing:
    def test_exposure_specs(self):
        assert parse_exposures("19-23,29") == (19, 20, 21, 22, 23, 29)
        assert parse_exposures([3, "5-6", 3]) == (3, 5, 6)
        assert parse_exposures(7) == (7,)
        for bad in ("5-3", "a", 0, [], True, "1,,2"):
            with pytest.raises(OverrideError):
                parse_exposures(bad)

    def test_shipped_file_is_valid(self):
        entries = load_overrides()
        assert entries, "the packaged override file should not be empty"
        vesta = [e for e in entries if e.id == "apo-55755-aadaf-vesta-f203"]
        assert vesta and vesta[0].fibers == {203: "VESTA"}
        assert vesta[0].exposures == (19, 22, 23, 29)
        # The Vesta exposures with no light on an identified fiber are not_science.
        bad = [e for e in entries if e.id == "apo-55755-aadaf-vesta-unconfirmed"]
        assert bad and bad[0].action == "not_science" and bad[0].exposures == (20, 21, 33)
        for e in entries:
            assert e.source_quote and e.source_file and e.reason

    def test_attrs(self):
        assert overrides_attrs(False) == {"exposure_overrides": "disabled"}
        on = overrides_attrs(True)
        assert on["exposure_overrides"] == "enabled" and len(on["exposure_overrides_sha256"]) == 64

    @pytest.mark.parametrize(
        "change, message",
        [
            (dict(action="delete_everything"), "unknown action"),
            (dict(colour="red"), "unknown key"),
            (dict(fibers={1: "X"}), "unknown key"),  # fibers only for retarget_fibers
            (dict(observatory="kpno"), "observatory"),
            (dict(mjd="55755"), "mjd"),
            (dict(reason=""), "reason"),
            (dict(source=dict(file="x")), "source"),
            (dict(source=dict(file="x", quote="")), "source.quote"),
            (dict(id="Bad Id"), "id"),
            (dict(action="use_config"), "missing key"),
            (dict(action="use_config", config_id=0), "config_id"),
            (dict(action="retarget_fibers", fibers={203: "VESTA"}), "missing key"),
            (dict(action="retarget_fibers", fibers={301: "VESTA"}, other_fibers="keep"), "1..300"),
            (dict(action="retarget_fibers", fibers={203: ""}, other_fibers="keep"), "target name"),
            (dict(action="retarget_fibers", fibers={203: "V"}, other_fibers="maybe"), "other_fibers"),
        ],
    )
    def test_malformed_entries_fail_loudly(self, change, message):
        e = entry()
        e.update(change)
        if change.get("source") is not None:
            e["source"] = change["source"]
        with pytest.raises(OverrideError, match=message):
            parse_overrides(doc(e))

    def test_malformed_file_fails_loudly(self):
        for text in ("not: [valid", "overrides: []", "version: 2\noverrides: []", "- 1\n- 2"):
            with pytest.raises(OverrideError):
                parse_overrides(text)

    def test_duplicate_ids(self):
        with pytest.raises(OverrideError, match="Duplicate"):
            parse_overrides(doc(entry(), entry(exposures="30")))

    def test_conflicting_entries(self):
        a = entry(id="a", action="retarget_fibers", fibers={203: "VESTA"}, other_fibers="keep")
        b = entry(id="b", action="retarget_fibers", fibers={203: "CERES"}, other_fibers="keep")
        with pytest.raises(OverrideError, match="conflicts"):
            parse_overrides(doc(a, b))
        c = entry(id="c", action="use_config", config_id=5)
        d = entry(id="d", action="use_previous_config")
        with pytest.raises(OverrideError, match="configuration override"):
            parse_overrides(doc(c, d))
        # Different fibers, both 'keep': allowed.
        e = entry(id="e", action="retarget_fibers", fibers={192: "CERES"}, other_fibers="keep")
        assert len(parse_overrides(doc(a, e))) == 2

    def test_overrides_for(self):
        entries = parse_overrides(doc(entry(), entry(id="other", mjd=55756)))
        assert [e.id for e in overrides_for("apo", 55755, entries)] == ["apo-55755-test"]
        assert overrides_for("lco", 55755, entries) == []


class TestApply:
    def test_no_override_leaves_exposure_untouched(self):
        e = make_exposures(1)[0]
        assert e.override_ids == "" and e.override_actions == ""
        assert e.fiber_map_key == "5099"
        assert not e.flagged_bad

    def test_mark_exposure_is_information_only(self):
        exposures = make_exposures()
        before = [x.model_dump() for x in exposures]
        apply_exposure_overrides(exposures, parse_overrides(doc(entry(exposures="2"))))
        after = [x.model_dump() for x in exposures]
        assert after[1]["override_ids"] == "apo-55755-test"
        assert after[1]["override_actions"] == "mark_exposure"
        for k in before[1]:
            if k not in ("override_ids", "override_actions"):
                assert repr(before[1][k]) == repr(after[1][k]), k
        assert before[0] == after[0]

    def test_not_science_sets_flagged_bad(self):
        exposures = make_exposures()
        apply_exposure_overrides(exposures, parse_overrides(doc(entry(action="not_science", exposures="3"))))
        assert exposures[2].flagged_bad and exposures[2].override_actions == "not_science"
        assert not exposures[1].flagged_bad

    def test_missing_exposure_fails_loudly(self):
        exposures = make_exposures(3)
        with pytest.raises(OverrideError, match="not found"):
            apply_exposure_overrides(exposures, parse_overrides(doc(entry(exposures="2-9"))))

    def test_retarget_fibers(self):
        exposures = make_exposures()
        # Touch the cached `targets` of every exposure first, as io/get_almanac_data may.
        for x in exposures:
            assert x.targets
        entries = parse_overrides(doc(
            entry(id="vesta", exposures="1,3", action="retarget_fibers", fibers={2: "VESTA"}, other_fibers="offtarget"),
            entry(id="star", exposures="4", action="retarget_fibers", fibers={3: "HD 1"}, other_fibers="keep"),
        ))
        apply_exposure_overrides(exposures, entries)
        apply_fiber_overrides(exposures, entries, TEST_TARGETS)

        e1, e2, e3, e4 = exposures[:4]
        # Exposure 1 was the sdss_id carrier and is overridden: the plain table
        # must still be written, with sdss_ids, by the first unaffected exposure.
        assert e2.fiber_map_key == "5099"
        assert [t.sdss_id for t in e2.targets] == [1001, 1002, 1003, -1]
        # Edited exposures get their own fiber-table ID in plate_id, one per entry set.
        assert e1.plate_id == e3.plate_id == 1005755001 and e1.fiber_map_key == "1005755001"
        assert e4.plate_id == 1005755002 and e4.fiber_map_key == "1005755002"
        assert e1.original_plate_id == e4.original_plate_id == e2.plate_id == 5099

        t = {x.fiber_id: x for x in e1.targets}
        # A solar-system body: no catalogue identity, sdss_id = solar_system_sdss_id.
        assert t[2].category == RETARGETED_CATEGORY and t[2].override_target == "VESTA"
        assert t[2].sdss_id == 0 and t[2].original_category == "science" and t[2].override_id == "vesta"
        assert t[2].twomass_designation == "" and t[2].catalogid == -1
        assert t[2].ra != t[2].ra and t[2].dec != t[2].dec  # NaN: no fixed position
        for f in (1, 3):
            assert t[f].category == OFF_TARGET_CATEGORY and t[f].sdss_id == -1 and t[f].override_id == "vesta"
        assert t[4].category == "sky_apogee" and t[4].override_id == ""

        t4 = {x.fiber_id: x for x in e4.targets}
        # A star: the row carries the star's targeting information, to be cross-matched.
        assert t4[3].category == "science" and t4[3].override_target == "HD 1" and t4[3].override_id == "star"
        assert t4[3].twomass_designation == "00050000+6748000" and (t4[3].ra, t4[3].dec) == (1.25, 67.8)
        assert t4[3].sdss_id == -1 and t4[3].original_category == "science"
        assert t4[1].category == "science" and t4[1].sdss_id == 1001 and t4[1].override_id == ""
        assert "retarget_fibers(2=VESTA;others=offtarget)" == e1.override_actions
        assert not assigns_sdss_id(RETARGETED_CATEGORY)

        # The overridden copies did not touch the reference table.
        assert [x.category for x in e2.targets] == ["science"] * 3 + ["sky_apogee"]

    def test_retarget_unknown_fiber_fails(self):
        exposures = make_exposures()
        entries = parse_overrides(doc(entry(exposures="2", action="retarget_fibers", fibers={250: "VESTA"}, other_fibers="keep")))
        apply_exposure_overrides(exposures, entries)
        with pytest.raises(OverrideError, match="fiber 250"):
            apply_fiber_overrides(exposures, entries)

    def test_retarget_requires_object(self):
        exposures = make_exposures()
        exposures[1].image_type = "dark"
        entries = parse_overrides(doc(entry(exposures="2", action="retarget_fibers", fibers={2: "V"}, other_fibers="keep")))
        with pytest.raises(OverrideError, match="not object"):
            apply_exposure_overrides(exposures, entries)

    def test_use_config_and_previous(self):
        exposures = []
        for i, cfg in enumerate((100, 100, 101, 101), start=1):
            exposures.append(Exposure(observatory="apo", mjd=60000, exposure=i, imagetyp="Object", configid=cfg))
        entries = parse_overrides(doc(
            entry(id="prev", mjd=60000, exposures="3", action="use_previous_config"),
            entry(id="given", mjd=60000, exposures="4", action="use_config", config_id=100),
        ))
        apply_exposure_overrides(exposures, entries)
        assert exposures[2].config_id == 100 and exposures[2].override_actions == "use_previous_config(101->100)"
        assert exposures[3].config_id == 100 and exposures[3].override_actions == "use_config(101->100)"
        assert exposures[3].fiber_map_key == "100"
        assert exposures[3].original_config_id == 101 and exposures[2].original_config_id == 101
        assert exposures[0].original_config_id == 100
        assert exposures[0].config_id == 100 and exposures[0].override_ids == ""

    def test_use_previous_config_without_previous_fails(self):
        exposures = [Exposure(observatory="apo", mjd=60000, exposure=1, imagetyp="Object", configid=7)]
        with pytest.raises(OverrideError, match="no earlier"):
            apply_exposure_overrides(exposures, parse_overrides(doc(entry(mjd=60000, exposures="1", action="use_previous_config"))))

    def test_written_columns(self, tmp_path):
        exposures = make_exposures(2)
        entries = parse_overrides(doc(entry(exposures="2", action="retarget_fibers", fibers={1: "VESTA"}, other_fibers="keep")))
        apply_exposure_overrides(exposures, entries)
        apply_fiber_overrides(exposures, entries)
        with h5.File(tmp_path / "t.h5", "w") as fp:
            write_models_to_hdf5_group(exposures, fp.create_group("exposures"))
            write_models_to_hdf5_group(exposures[1].targets, fp.create_group("fibers"))
            assert [x.decode() for x in fp["exposures/override_ids"][:]] == ["", "apo-55755-test"]
            assert [x.decode() for x in fp["exposures/fiber_map_key"][:]] == ["5099", "1005755001"]
            assert list(fp["exposures/plate_id"][:]) == [5099, 1005755001]
            assert list(fp["exposures/original_plate_id"][:]) == [5099, 5099]
            assert fp["fibers/category"][0].decode() == RETARGETED_CATEGORY
            assert fp["fibers/override_target"][0].decode() == "VESTA"


class TestConfigFields:
    """Optional recorded_config / actual_config (FPS configuration entries)."""

    @pytest.mark.parametrize(
        "change, message",
        [
            (dict(recorded_config=5), "together"),
            (dict(actual_config=5), "together"),
            (dict(recorded_config=0, actual_config=5), "recorded_config must be a positive int"),
            (dict(recorded_config=5, actual_config="4"), "actual_config must be a positive int"),
            (dict(recorded_config=True, actual_config=4), "recorded_config must be a positive int"),
            (dict(recorded_config=5, actual_config=5), "must differ"),
            (dict(action="use_config", config_id=3, recorded_config=5, actual_config=4), "equal config_id"),
            (dict(action="retarget_fibers", fibers={2: "V"}, other_fibers="keep", recorded_config=5, actual_config=4),
             "FPS configuration entries"),
        ],
    )
    def test_invalid(self, change, message):
        e = entry(mjd=60285)
        e.update(change)
        with pytest.raises(OverrideError, match=message):
            parse_overrides(doc(e))

    def test_valid_and_checked_on_apply(self):
        exposures = [Exposure(observatory="apo", mjd=60285, exposure=i, imagetyp="Object", configid=c)
                     for i, c in ((1, 11896), (2, 11897))]
        ok = parse_overrides(doc(entry(mjd=60285, exposures="2", action="not_science",
                                       recorded_config=11897, actual_config=11896)))
        assert (ok[0].recorded_config, ok[0].actual_config) == (11897, 11896)
        apply_exposure_overrides(exposures, ok)
        assert exposures[1].flagged_bad and not exposures[0].flagged_bad

        # The entry names a header configuration the exposure does not have.
        bad = parse_overrides(doc(entry(mjd=60285, exposures="1", action="not_science",
                                        recorded_config=11897, actual_config=11896)))
        with pytest.raises(OverrideError, match="not recorded_config"):
            apply_exposure_overrides(exposures, bad)

        # use_config with matching actual_config is accepted and applied.
        exposures = [Exposure(observatory="apo", mjd=60285, exposure=1, imagetyp="Object", configid=11897)]
        uc = parse_overrides(doc(entry(mjd=60285, exposures="1", action="use_config", config_id=11896,
                                       recorded_config=11897, actual_config=11896)))
        apply_exposure_overrides(exposures, uc)
        assert exposures[0].config_id == 11896


# ---------------------------------------------------------------------------
# The generated "robots not moved" entries (scripts/robots_not_moved/).
# ---------------------------------------------------------------------------

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "robots_not_moved" / "robots_not_moved.py"


def _generator():
    spec = importlib.util.spec_from_file_location("robots_not_moved", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _robots_entries():
    return [e for e in load_overrides() if "-robots-not-moved-" in e.id]


def _mock_night(observatory, mjd, entries, extra=2):
    """
    FPS object exposures 1..N+extra of one night: listed exposures carry their
    entry's recorded (header) configuration, all others the actual one.
    """
    header = {}
    for e in entries:
        for x in e.exposures:
            header[x] = e.recorded_config
    fallback = entries[0].actual_config
    n = max(header) + extra
    return [
        Exposure(observatory=observatory, mjd=mjd, exposure=i, imagetyp="Object", configid=header.get(i, fallback))
        for i in range(1, n + 1)
    ]


class TestRobotsNotMoved:
    def test_shipped_entries(self):
        entries = _robots_entries()
        exposures = {(e.observatory, e.mjd, x) for e in entries for x in e.exposures}
        # 94 exposures: 71 APO on 29 nights, 23 LCO on 16 nights (see the README).
        assert len(exposures) == 94
        assert sum(1 for k in exposures if k[0] == "apo") == 71
        assert len({k[:2] for k in exposures if k[0] == "apo"}) == 29
        assert len({k[:2] for k in exposures if k[0] == "lco"}) == 16
        for e in entries:
            assert e.action == "not_science"
            assert e.recorded_config > 0 and e.actual_config > 0 and e.recorded_config != e.actual_config
            assert e.id == f"{e.observatory}-{e.mjd}-robots-not-moved-a{e.actual_config}-b{e.recorded_config}"
            assert str(e.recorded_config) in e.reason and str(e.actual_config) in e.reason
            assert "data check" in e.reason
            assert "configuration_loaded=" in e.source_quote and "Starting trajectory" in e.source_quote
            # Excluded: LCO before MJD 60000 (the signature is false there).
            assert not (e.observatory == "lco" and e.mjd < 60000)
        # The LCO 59860 exposures whose light follows the recorded configuration are not listed.
        assert not any(k[:2] == ("lco", 59860) for k in exposures)
        # A few named cases.
        for k in [("apo", 60285, 43), ("apo", 60285, 44), ("apo", 60450, 22), ("apo", 60450, 23),
                  ("lco", 61210, 47), ("lco", 60481, 49), ("apo", 59914, 67)]:
            assert k in exposures, k

    def test_generated_block_is_current(self):
        gen = _generator()
        listed, excluded = gen.select(gen.read_candidates())
        block = gen.render_block(gen.group_entries(listed))
        text = (Path(__file__).resolve().parents[1] / "src" / "almanac" / "etc" / "exposure_overrides.yaml").read_text()
        assert gen.current_block(text) == block, "regenerate with robots_not_moved.py overrides --write-yaml"
        assert len(listed) == 94 and len(excluded) == 3

    def test_header_note(self):
        text = (Path(__file__).resolve().parents[1] / "src" / "almanac" / "etc" / "exposure_overrides.yaml").read_text()
        head = text.split("version: 1")[0]
        assert "marked bad (not_science) for 0.4.1" in head
        assert "use_config <A>" in head and "WHOLE exposure" in head

    def test_every_listed_exposure_exists_and_is_flagged(self):
        """Apply every shipped entry to a mock night: no entry may fail, all listed are flagged."""
        entries = _robots_entries()
        nights = sorted({(e.observatory, e.mjd) for e in entries})
        for observatory, mjd in nights:
            night = overrides_for(observatory, mjd)
            assert night
            exposures = _mock_night(observatory, mjd, night)
            apply_exposure_overrides(exposures, night)
            listed = {x for e in night for x in e.exposures}
            for x in exposures:
                if x.exposure in listed:
                    assert x.flagged_bad and "not_science" in x.override_actions, (observatory, mjd, x.exposure)
                    assert x.config_id in {e.recorded_config for e in night}  # header kept
                else:
                    assert not x.flagged_bad and x.override_ids == "", (observatory, mjd, x.exposure)

    def _night_60285(self):
        # APO 60285: 42 is the previous configuration (A), 43-44 recorded B but
        # the robots were at A, 45 is after the real move to B.
        return [
            Exposure(observatory="apo", mjd=60285, exposure=i, imagetyp="Object", configid=c)
            for i, c in ((42, 11896), (43, 11897), (44, 11897), (45, 11897))
        ]

    def test_get_almanac_data_and_no_overrides(self, monkeypatch):
        import almanac.apogee as apogee

        monkeypatch.setattr(apogee, "get_exposures_and_missing", lambda o, m: (self._night_60285(), []))
        *_, exposures, _, _ = apogee.get_almanac_data("apo", 60285)
        by = {e.exposure: e for e in exposures}
        assert by[43].flagged_bad and by[44].flagged_bad
        assert by[43].override_ids == "apo-60285-robots-not-moved-a11896-b11897"
        assert not by[42].flagged_bad and not by[45].flagged_bad and by[45].override_ids == ""
        assert by[43].config_id == 11897  # not_science does not change the configuration

        *_, exposures, _, _ = apogee.get_almanac_data("apo", 60285, overrides=False)
        assert not any(e.flagged_bad for e in exposures)
        assert all(e.override_ids == "" for e in exposures)

    def test_cli_no_overrides(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        import almanac.apogee as apogee
        from almanac.cli import main

        monkeypatch.setattr(apogee, "get_exposures_and_missing", lambda o, m: (self._night_60285(), []))
        results = {}
        for flag in ([], ["--no-overrides"]):
            output = str(tmp_path / f"out{len(flag)}.h5")
            r = CliRunner().invoke(main, ["--mjd", "60285", "--apo", "--output", output, *flag],
                                   catch_exceptions=False)
            assert r.exit_code == 0, r.output
            with h5.File(output, "r") as fp:
                g = fp["raw/apo/60285"]
                results[bool(flag)] = (
                    list(g["exposures/exposure"][:]),
                    [bool(x) for x in g["exposures/flagged_bad"][:]],
                    g.attrs["exposure_overrides"],
                )
        assert results[False] == ([42, 43, 44, 45], [False, True, True, False], "enabled")
        assert results[True] == ([42, 43, 44, 45], [False, False, False, False], "disabled")
