"""`apogee.cross_match_targets` and `io.write_fibers` (extracted upstream in #66, fork bodies)."""

import h5py as h5
import numpy as np

from almanac import apogee, io
from almanac.data_models.fps import FPSTarget
from tests.helpers import MJD, SEQUENCES, make_exposures


def _with_targets(exposures, targets):
    for e in exposures:
        e._targets = tuple(targets) if e.image_type == "object" else tuple()
    return exposures


def _targets():
    return (
        FPSTarget(category="science", racat=10.0, deccat=-20.0, fiberId=1, catalogid=5_000_000_001),
        FPSTarget(category="science", racat=10.5, deccat=-20.5, fiberId=2, catalogid=5_000_000_002),
        FPSTarget(category="unplugged", racat=11.0, deccat=-21.0, fiberId=3, catalogid=5_000_000_003),
    )


class TestCrossMatchTargets:
    def test_uses_fork_helpers_and_withholds_non_assigning(self, monkeypatch):
        import almanac.database as database

        monkeypatch.setattr(database, "is_database_available", True, raising=False)
        seen = []

        def fake_catalogid_lookup(ids):
            seen.append(set(ids))
            return {5_000_000_001: 11, 5_000_000_003: 33}

        monkeypatch.setattr(apogee, "catalogid_sdss_id_lookup", fake_catalogid_lookup)
        exposures = _with_targets(make_exposures(), _targets())
        apogee.cross_match_targets(exposures, SEQUENCES["objects"], xmatch=True)

        assert seen == [{5_000_000_001, 5_000_000_002, 5_000_000_003}]
        first = exposures[0].targets
        assert [t.sdss_id for t in first] == [11, -1, -1]  # unplugged keeps -1

    def test_no_xmatch_does_not_touch_database(self, monkeypatch):
        monkeypatch.setattr(
            apogee, "catalogid_sdss_id_lookup",
            lambda ids: (_ for _ in ()).throw(AssertionError("queried")),
        )
        exposures = _with_targets(make_exposures(), _targets())
        apogee.cross_match_targets(exposures, SEQUENCES["objects"], xmatch=False)
        assert all(t.sdss_id == -1 for t in exposures[0].targets)


class TestWriteFibers:
    def test_one_group_per_key_and_update_delegates(self, tmp_path):
        path = str(tmp_path / "night.h5")
        exposures = _with_targets(make_exposures(), _targets())
        with h5.File(path, "a") as fp:
            io.update(fp, "apo", MJD, exposures, SEQUENCES, fibers=True)
        with h5.File(path, "r") as fp:
            assert list(fp[f"raw/apo/{MJD}/fibers"]) == ["123"]
            assert fp[f"raw/apo/{MJD}/fibers/123/fiber_id"][:].tolist() == [1, 2, 3]

    def test_fiber_map_key_override_gets_own_group_and_replaces(self, tmp_path):
        path = str(tmp_path / "night.h5")
        exposures = _with_targets(make_exposures(), _targets())
        # An override that edits fibers gives its exposure a synthetic id (#20).
        exposures[1].config_id = 1_000_000_123
        exposures[1]._targets = tuple(_targets()[:2])
        with h5.File(path, "a") as fp:
            io.write_fibers(fp, "apo", MJD, exposures)
            io.write_fibers(fp, "apo", MJD, exposures)  # replaces in place
        with h5.File(path, "r") as fp:
            assert sorted(fp[f"raw/apo/{MJD}/fibers"]) == ["1000000123", "123"]
            assert len(fp[f"raw/apo/{MJD}/fibers/1000000123/fiber_id"]) == 2
