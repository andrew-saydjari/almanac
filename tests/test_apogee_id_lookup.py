"""Tests for parent-built APOGEE ID lookup sharing with pool workers."""

import pytest

import almanac.apogee as apogee
from almanac.apogee import any_plate_era, set_apogee_id_lookup
from almanac.cli import _pool_initializer
from almanac.data_models.exposure import FPS_ERA_START_MJD


@pytest.fixture(autouse=True)
def _reset_lookup_global():
    original = apogee.APOGEE_ID_LOOKUP
    yield
    apogee.APOGEE_ID_LOOKUP = original


class TestAnyPlateEra:
    def test_boundaries(self):
        assert FPS_ERA_START_MJD == dict(apo=59423, lco=59809)
        # Last plate-era night is plate era; first FPS night is not.
        assert any_plate_era([(59422, "apo")])
        assert not any_plate_era([(59423, "apo")])
        assert any_plate_era([(59808, "lco")])
        assert not any_plate_era([(59809, "lco")])

    def test_mixed_tasks(self):
        assert any_plate_era([(60000, "apo"), (57800, "apo")])
        assert not any_plate_era([(60000, "apo"), (60000, "lco")])
        assert not any_plate_era([])

    def test_lco_plate_apo_fps_gap(self):
        # MJD in the window where APO is FPS but LCO is still plate era.
        assert any_plate_era([(59500, "lco")])
        assert not any_plate_era([(59500, "apo")])


class TestSetApogeeIdLookup:
    def test_installs_global(self):
        lookup = {"2M00000000+0000000": 123}
        set_apogee_id_lookup(lookup)
        assert apogee.APOGEE_ID_LOOKUP is lookup

    def test_overwrites_existing(self):
        set_apogee_id_lookup({"a": 1})
        replacement = {"b": 2}
        set_apogee_id_lookup(replacement)
        assert apogee.APOGEE_ID_LOOKUP is replacement


class TestPoolInitializer:
    def test_installs_lookup_in_worker(self):
        lookup = {"2M12345678+1234567": 42}
        _pool_initializer(lookup)
        assert apogee.APOGEE_ID_LOOKUP is lookup

    def test_without_lookup_uses_targeted_queries(self):
        apogee.APOGEE_ID_LOOKUP = None
        _pool_initializer()
        # No full lookup shipped: the worker queries only the APOGEE IDs its
        # nights need (see test_targeted_apogee_id_lookup.py).
        assert apogee.APOGEE_ID_LOOKUP is None

    def test_worker_with_lookup_does_not_query(self, monkeypatch):
        lookup = {"11111111+1111111": 7}
        _pool_initializer(lookup)

        def _fail(*args, **kwargs):
            raise AssertionError("worker should not query the database")

        monkeypatch.setattr(apogee, "create_apogee_id_lookup", _fail)
        monkeypatch.setattr(apogee, "_query_apogee_id_rows", _fail)
        # The call used inside get_almanac_data:
        assert apogee.get_apogee_id_lookup(["11111111+1111111"]) is lookup
