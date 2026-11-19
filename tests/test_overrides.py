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
        assert vesta[0].exposures == (19, 20, 21, 22, 23, 29)
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
        apply_fiber_overrides(exposures, entries)

        e1, e2, e3, e4 = exposures[:4]
        # Exposure 1 was the sdss_id carrier and is overridden: the plain table
        # must still be written, with sdss_ids, by the first unaffected exposure.
        assert e2.fiber_map_key == "5099"
        assert [t.sdss_id for t in e2.targets] == [1001, 1002, 1003, -1]
        assert e1.fiber_map_key == "5099.override.vesta"
        assert e3.fiber_map_key == "5099.override.vesta"
        assert e4.fiber_map_key == "5099.override.star"

        t = {x.fiber_id: x for x in e1.targets}
        assert t[2].category == RETARGETED_CATEGORY and t[2].override_target == "VESTA"
        assert t[2].sdss_id == -1 and t[2].original_category == "science" and t[2].override_id == "vesta"
        assert t[2].twomass_designation == "2M00000002-0000000"  # original identity kept for provenance
        for f in (1, 3):
            assert t[f].category == OFF_TARGET_CATEGORY and t[f].sdss_id == -1 and t[f].override_id == "vesta"
        assert t[4].category == "sky_apogee" and t[4].override_id == ""

        t4 = {x.fiber_id: x for x in e4.targets}
        assert t4[3].category == RETARGETED_CATEGORY and t4[3].override_target == "HD 1"
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
            assert [x.decode() for x in fp["exposures/fiber_map_key"][:]] == ["5099", "5099.override.apo-55755-test"]
            assert fp["fibers/category"][0].decode() == RETARGETED_CATEGORY
            assert fp["fibers/override_target"][0].decode() == "VESTA"
