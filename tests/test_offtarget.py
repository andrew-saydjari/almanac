"""Tests for the `offtarget` relabeling of FPS fibers that missed their target."""

import h5py as h5
import pytest

from almanac.data_models.fps import FPSTarget
from almanac.data_models.plate import PlateTarget
from almanac.data_models.types import (
    OFF_TARGET_CATEGORY,
    asserts_target_identity,
    assigns_sdss_id,
)
from almanac.io import write_models_to_hdf5_group


def fps_target(**kwargs):
    """An FPS fiber row, with the fields a confSummary file would supply."""
    row = dict(
        sdss_id=123456,
        catalogid=63050395538566407,
        category="science",
        positionerId=343,
        holeId="R+11C1",
        holeType="fps",
        holetype="fps",
        fiberType="APOGEE",
        fiberId=139,
        spectrographId=2,
        assigned=1,
        on_target=1,
        valid=1,
        disabled=0,
        decollided=0,
        racat=278.3688249947745,
        deccat=-11.637080819090022,
    )
    row.update(kwargs)
    return FPSTarget(**row)


class TestOffTargetRelabel:
    def test_off_target_science_fiber_is_relabeled(self):
        target = fps_target(on_target=0, decollided=1)
        assert target.category == OFF_TARGET_CATEGORY
        assert target.original_category == "science"
        assert target.off_target

    def test_off_target_fiber_has_no_sdss_id(self):
        target = fps_target(on_target=0, sdss_id=85604486)
        assert target.sdss_id == -1
        assert not target.expected_to_be_assigned_sdss_id

    def test_on_target_fiber_is_untouched(self):
        target = fps_target(on_target=1, sdss_id=85604486)
        assert target.category == "science"
        assert target.original_category == ""
        assert target.sdss_id == 85604486
        assert not target.off_target
        assert target.expected_to_be_assigned_sdss_id

    def test_unassigned_fiber_is_untouched(self):
        # A fiber with no APOGEE assignment (likely BOSS instead) is not
        # off-target: there was no target for it to miss.
        target = fps_target(assigned=0, on_target=0, category="")
        assert target.category == ""
        assert target.original_category == ""
        assert not target.off_target

    def test_decollided_but_on_target_is_untouched(self):
        # `decollided` is not the predicate: a positioner can be moved to
        # resolve a collision and still land on its target.
        target = fps_target(on_target=1, decollided=1, sdss_id=85604486)
        assert target.category == "science"
        assert target.sdss_id == 85604486

    @pytest.mark.parametrize(
        "category", (
            "science", "veto_location_boss", "veto_location_apogee",
            "standard_apogee", "standard_boss", "open_fiber", "bonus",
        )
    )
    def test_all_target_asserting_categories_are_relabeled(self, category):
        target = fps_target(category=category, on_target=0)
        assert target.category == OFF_TARGET_CATEGORY
        assert target.original_category == category
        assert target.sdss_id == -1

    @pytest.mark.parametrize("category", ("sky_apogee", "sky_boss", "unplugged", ""))
    def test_categories_without_target_identity_are_not_relabeled(self, category):
        # Sky and unplugged fibers make no claim about which target is in the
        # fiber, so there is nothing to invalidate.
        target = fps_target(category=category, on_target=0)
        assert target.category == category
        assert target.original_category == ""

    def test_missing_flags_do_not_relabel(self):
        # If a configuration summary file did not report `on_target` at all, it
        # would default to False; we must not read that as "missed its target".
        row = dict(
            sdss_id=85604486,
            catalogid=63050395538566407,
            category="science",
            fiberId=139,
            racat=278.3688249947745,
            deccat=-11.637080819090022,
            assigned=1,
        )
        target = FPSTarget(**row)
        assert target.category == "science"
        assert target.original_category == ""
        assert target.sdss_id == 85604486

    def test_relabeling_is_idempotent(self):
        target = fps_target(on_target=0)
        again = FPSTarget(**target.model_dump())
        assert again.category == OFF_TARGET_CATEGORY
        assert again.original_category == "science"
        assert again.sdss_id == -1

    def test_raw_configuration_columns_are_preserved(self):
        # The serendipitous-recovery work needs to be able to find these rows
        # and reconstruct where the fiber actually pointed, so none of the raw
        # confSummary columns may be altered by the relabeling.
        target = fps_target(
            on_target=0,
            decollided=1,
            valid=1,
            disabled=0,
            alpha=82.19706726074219,
            beta=102.98738861083984,
            xwok=-162.086318652563,
            ywok=226.81407774543203,
        )
        assert target.assigned is True
        assert target.on_target is False
        assert target.decollided is True
        assert target.valid is True
        assert target.disabled is False
        assert target.catalogid == 63050395538566407
        assert target.positioner_id == 343
        assert target.fiber_id == 139
        assert target.alpha == 82.19706726074219
        assert target.beta == 102.98738861083984
        assert target.ra == 278.3688249947745
        assert target.dec == -11.637080819090022


class TestTask94Fiber:
    """
    The failure that motivated this: `sdss_id` 85604486 on apo MJD 60774,
    configuration 20267. Positioner 343 was decollided away from its assigned
    target and the fiber observed a patch of sky 9 arcmin away, but the almanac
    kept reporting the fiber as `science` with the assigned star's `sdss_id`,
    so a field star's radial velocity was published under the target's name.
    """

    def build(self):
        return FPSTarget(
            sdss_id=85604486,
            catalogid=63050395538566407,
            category="science",
            positionerId=343,
            holeId="R+11C1",
            holeType="fps",
            holetype="fps",
            fiberType="APOGEE",
            fiberId=139,
            spectrographId=2,
            assigned=1,
            on_target=0,
            valid=1,
            disabled=0,
            decollided=1,
            alpha=82.19706726074219,
            beta=102.98738861083984,
            xwok=-162.086318652563,
            ywok=226.81407774543203,
            racat=278.3688249947745,
            deccat=-11.637080819090022,
        )

    def test_fiber_is_offtarget_without_an_sdss_id(self):
        target = self.build()
        assert target.category == OFF_TARGET_CATEGORY
        assert target.original_category == "science"
        assert target.sdss_id == -1

    def test_flags_still_explain_why(self):
        target = self.build()
        assert target.assigned is True
        assert target.on_target is False
        assert target.decollided is True
        assert target.valid is True
        assert target.disabled is False


class TestCategoryHelpers:
    @pytest.mark.parametrize(
        "category", (
            "science", "veto_location_boss", "veto_location_apogee",
            "standard_apogee", "standard_boss", "open_fiber", "bonus",
        )
    )
    def test_target_asserting_categories(self, category):
        assert asserts_target_identity(category)
        assert assigns_sdss_id(category)

    @pytest.mark.parametrize("category", ("sky_apogee", "sky_boss", ""))
    def test_categories_that_assert_nothing(self, category):
        assert not asserts_target_identity(category)

    @pytest.mark.parametrize("category", ("unplugged", OFF_TARGET_CATEGORY))
    def test_categories_that_never_get_an_sdss_id(self, category):
        assert not assigns_sdss_id(category)
        assert not asserts_target_identity(category)


class TestOffTargetInHDF5:
    def test_written_fiber_table(self, tmp_path):
        targets = [
            fps_target(fiberId=138, on_target=1, sdss_id=85604486),
            fps_target(fiberId=139, on_target=0, decollided=1, sdss_id=85604486),
            fps_target(fiberId=140, category="sky_apogee", on_target=0, sdss_id=-1),
        ]
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_models_to_hdf5_group(targets, fp.create_group("fibers"))

            group = fp["fibers"]
            categories = group["category"][:].astype(str)
            assert list(categories) == ["science", OFF_TARGET_CATEGORY, "sky_apogee"]
            assert list(group["original_category"][:].astype(str)) == ["", "science", ""]
            assert list(group["sdss_id"][:]) == [85604486, -1, -1]

            # The raw flags that identify the failure must survive.
            for field in ("assigned", "on_target", "valid", "disabled", "decollided"):
                assert field in group
            assert list(group["on_target"][:]) == [True, False, False]
            assert list(group["decollided"][:]) == [False, True, False]

    def test_category_dtype_is_unchanged_by_the_new_value(self, tmp_path):
        # "offtarget" is shorter than the longest existing category, so adding
        # it does not widen the `category` column.
        with h5.File(tmp_path / "test.h5", "a") as fp:
            write_models_to_hdf5_group(
                [fps_target(on_target=0)], fp.create_group("fibers")
            )
            assert fp["fibers"]["category"].dtype == fp["fibers"]["original_category"].dtype


class TestPlateTargetIsUnaffected:
    def test_plate_era_has_no_off_target_concept(self):
        # Plate-era rows carry no `on_target` column, and must not be relabeled.
        target = PlateTarget(
            sdss_id=85604486,
            catalogid=63050395538566407,
            targettype="science",
            observatory="apo",
            holeType="OBJECT",
            holetype="OBJECT",
            fiberId=139,
            ra=278.3688249947745,
            dec=-11.637080819090022,
        )
        assert target.category == "science"
        assert target.sdss_id == 85604486
