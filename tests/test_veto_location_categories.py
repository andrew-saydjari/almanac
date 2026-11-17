"""
Tests for the `veto_location_boss` and `veto_location_apogee` FPS categories.

Manually built FPS designs (first seen in apo configurations 27968, 28001, 28004
and 28007) place APOGEE fibers on real catalog stars at "veto locations": Gaia
bright neighbors (`veto_location_boss`, carton `ops_gaia_brightneighbors`) and
bright 2MASS stars (`veto_location_apogee`, carton
`ops_2mass_psc_brightneighbors`). Before these categories were known, the
`Category` literal rejected them and the whole night failed to build.

The fixture `data/confSummaryFS-28001-trimmed.par` is the real
confSummaryFS-28001.par (apo MJD 61335) with the header and typedefs verbatim and
the FIBERMAP trimmed to 9 rows:

    positioner  fiberType  category              on_target
    3           APOGEE     veto_location_boss    1
    16          APOGEE     veto_location_boss    1
    493         APOGEE     veto_location_boss    0
    167         APOGEE     veto_location_apogee  1
    548         APOGEE     veto_location_apogee  1
    973         APOGEE     veto_location_apogee  0
    264         APOGEE     standard_apogee       1
    117         APOGEE     sky_apogee            1
    3           BOSS       (empty)               0
"""
import os
import shutil

import h5py as h5
import numpy as np
import pytest
from astropy.table import Table
from click.testing import CliRunner
from pydantic import ValidationError

from almanac import config
from almanac.apogee import parse_target_identifier
from almanac.data_models.exposure import Exposure
from almanac.data_models.fps import FPSTarget
from almanac.data_models.types import IDENTIFIED_STAR_CATEGORIES

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "confSummaryFS-28001-trimmed.par")
CONFIG_ID = 28001
VETO_CATEGORIES = ("veto_location_boss", "veto_location_apogee")

# fiber_id -> (category, on_target), for the APOGEE rows of the fixture
EXPECTED = {
    214: ("veto_location_boss", True),
    102: ("veto_location_boss", True),
    78: ("veto_location_boss", False),
    251: ("veto_location_apogee", True),
    13: ("veto_location_apogee", True),
    39: ("veto_location_apogee", False),
    136: ("standard_apogee", True),
    244: ("sky_apogee", True),
}


@pytest.fixture
def sdsscore(tmp_path, monkeypatch):
    directory = tmp_path / "apo" / "summary_files" / "028XXX" / "0280XX"
    directory.mkdir(parents=True)
    shutil.copy(FIXTURE, directory / f"confSummaryFS-{CONFIG_ID}.par")
    monkeypatch.setattr(config, "sdsscore_dir", str(tmp_path))
    return directory


def _exposure():
    return Exposure(
        observatory="apo", mjd=61335, exposure=1, image_type="object", config_id=CONFIG_ID
    )


def _real_targets(exposure):
    # Exposure.targets pads missing fiber IDs (1..300) with "bonus" rows.
    return {t.fiber_id: t for t in exposure.targets if t.category != "bonus"}


def _reported_category(target):
    # The category the confSummary file reported. If off-target relabeling is
    # in place (fork PR #10), an off-target fiber's `category` becomes
    # "offtarget" and the reported one moves to `original_category`.
    return getattr(target, "original_category", "") or target.category


def test_veto_location_rows_validate_and_keep_their_category(sdsscore):
    targets = _real_targets(_exposure())
    assert set(targets) == set(EXPECTED)
    for fiber_id, (category, on_target) in EXPECTED.items():
        target = targets[fiber_id]
        assert target.on_target is on_target
        assert _reported_category(target) == category
        if on_target:
            # Never relabeled (e.g. to "science"): the category is kept verbatim.
            assert target.category == category
        else:
            # Off-target fibers keep their category too, unless off-target
            # relabeling is in place, in which case they become "offtarget",
            # exactly like an off-target science fiber.
            assert target.category in (category, "offtarget")


def test_veto_location_rows_carry_catalog_identity(sdsscore):
    targets = _real_targets(_exposure())
    veto = [t for t in targets.values() if _reported_category(t) in VETO_CATEGORIES]
    assert len(veto) == 6
    for target in veto:
        assert target.assigned
        assert target.catalogid > 0
        assert target.firstcarton in (
            "ops_gaia_brightneighbors", "ops_2mass_psc_brightneighbors"
        )


def test_veto_location_rows_are_eligible_for_sdss_id_cross_match(sdsscore):
    targets = _real_targets(_exposure())
    for target in targets.values():
        if target.on_target and target.category in VETO_CATEGORIES + ("standard_apogee",):
            assert target.expected_to_be_assigned_sdss_id
            # These are v1 catalogids, so the cross-match goes straight
            # through SDSS_ID_flat on catalogid, as for science targets.
            assert parse_target_identifier(target) == ("catalogid", target.catalogid)
        elif target.category == "sky_apogee":
            assert not target.expected_to_be_assigned_sdss_id


def test_veto_location_categories_are_identified_stars():
    assert "science" in IDENTIFIED_STAR_CATEGORIES
    for category in VETO_CATEGORIES:
        assert category in IDENTIFIED_STAR_CATEGORIES
    for category in ("", "bonus", "sky_apogee", "sky_boss", "unplugged"):
        assert category not in IDENTIFIED_STAR_CATEGORIES


def test_unknown_category_still_fails_loudly():
    with pytest.raises(ValidationError):
        FPSTarget(category="veto_location_unknown", racat=0.0, deccat=0.0)


def test_unknown_category_in_confsummary_fails_loudly(tmp_path, monkeypatch):
    directory = tmp_path / "apo" / "summary_files" / "028XXX" / "0280XX"
    directory.mkdir(parents=True)
    with open(FIXTURE) as fp:
        content = fp.read()
    assert content.count(" veto_location_apogee ") == 3
    with open(directory / f"confSummaryFS-{CONFIG_ID}.par", "w") as fp:
        fp.write(content.replace(" veto_location_apogee ", " not_a_category ", 1))
    monkeypatch.setattr(config, "sdsscore_dir", str(tmp_path))
    with pytest.raises(ValidationError):
        _exposure().targets


def _write_almanac_file(path, targets, sdss_ids):
    """Write a minimal almanac HDF5 file with one FPS exposure of CONFIG_ID."""
    targets = list(targets)
    with h5.File(path, "w") as fp:
        group = fp.create_group("raw/apo/61335")
        group.create_dataset("exposures/image_type", data=np.array([b"object"]))
        group.create_dataset("exposures/config_id", data=np.array([CONFIG_ID]))
        group.create_dataset("exposures/plate_id", data=np.array([-1]))
        fibers = group.create_group(f"fibers/{CONFIG_ID}")
        fibers.create_dataset(
            "category", data=np.array([t.category.encode() for t in targets])
        )
        fibers.create_dataset("catalogid", data=np.array([t.catalogid for t in targets]))
        fibers.create_dataset(
            "twomass_designation",
            data=np.array([t.twomass_designation.encode() for t in targets], dtype="S32"),
        )
        fibers.create_dataset(
            "sdss_id", data=np.array([sdss_ids.get(t.fiber_id, -1) for t in targets])
        )


def test_veto_location_fibers_are_included_in_star_list(sdsscore, tmp_path):
    from almanac.cli import main

    targets = _real_targets(_exposure())
    # Pretend the cross-match succeeded for every fiber with a catalog identity,
    # with a distinct sdss_id per fiber.
    sdss_ids = {
        fiber_id: 1000 + fiber_id
        for fiber_id, target in targets.items()
        if target.expected_to_be_assigned_sdss_id
    }
    input_path = str(tmp_path / "almanac.h5")
    output_path = str(tmp_path / "stars.csv")
    _write_almanac_file(input_path, targets.values(), sdss_ids)

    result = CliRunner().invoke(main, ["dump", "stars", input_path, output_path])
    assert result.exit_code == 0, result.output

    stars = Table.read(output_path, format="ascii.csv")
    listed = set(stars["sdss_id"])
    for fiber_id, (category, on_target) in EXPECTED.items():
        target = targets[fiber_id]
        if category == "sky_apogee":
            assert 1000 + fiber_id not in listed, (fiber_id, category)
        elif on_target or target.category != "offtarget":
            # Veto-location fibers are listed exactly like science fibers
            # (and like the standard_apogee fiber here).
            assert 1000 + fiber_id in listed, (fiber_id, category)
        else:
            # With off-target relabeling in place, an off-target veto-location
            # fiber has no sdss_id and is not listed, like an off-target
            # science fiber.
            assert 1000 + fiber_id not in listed, (fiber_id, category)
