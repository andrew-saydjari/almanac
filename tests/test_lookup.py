"""`almanac lookup` (bug G): no debug output; -O writes nights with night attributes."""

import ast
from pathlib import Path

import h5py as h5

import almanac.apogee as apogee
import almanac.cli
from almanac.cli import _lookup_night_writer
from almanac.overrides import overrides_attrs
from tests.helpers import MJD, SEQUENCES, fake_targets, make_exposures


def test_lookup_has_no_debug_prints():
    tree = ast.parse(Path(almanac.cli.__file__).read_text())
    lookup = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "lookup")
    prints = [n for n in ast.walk(lookup)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print"]
    assert prints == []


def test_lookup_output_writes_night_attrs(tmp_path, monkeypatch):
    lookup_attrs = {"apogee_id_lookup_source": "materialized_view"}

    def fake(observatory, mjd, fibers=False, meta=False, **kwargs):
        exposures = make_exposures(observatory, mjd)
        for e in exposures:
            e._targets = fake_targets(e) if e.image_type == "object" else ()
        return observatory, mjd, exposures, dict(SEQUENCES), []

    monkeypatch.setattr(apogee, "get_almanac_data", fake)
    monkeypatch.setattr(apogee, "prepare_cross_match", lambda *a, **k: (None, lookup_attrs, None))
    out = str(tmp_path / "lookup.h5")
    _lookup_night_writer(out)("apo", MJD)
    with h5.File(out, "r") as fp:
        attrs = dict(fp[f"raw/apo/{MJD}"].attrs)
        assert f"raw/apo/{MJD}/fibers/123" in fp
    assert attrs == {**overrides_attrs(True), **lookup_attrs}
