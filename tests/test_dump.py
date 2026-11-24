"""`almanac dump` subcommands (bug F)."""

import h5py as h5
import numpy as np
from astropy.table import Table

from almanac import io
from almanac.data_models import Exposure
from almanac.data_models.fps import FPSTarget
from almanac.data_models.plate import PlateTarget
from tests.helpers import SEQUENCES, make_exposures, run_cli


def fps_night(fp, mjd=60000):
    exposures = make_exposures("apo", mjd)
    targets = (
        FPSTarget(category="science", racat=1.0, deccat=2.0, fiberId=1, catalogid=5, sdss_id=11),
        FPSTarget(category="science", racat=1.0, deccat=2.0, fiberId=2, catalogid=6, sdss_id=-1),
        FPSTarget(category="science", racat=1.0, deccat=2.0, fiberId=3, sdss_id=0),
        FPSTarget(category="sky_apogee", racat=1.0, deccat=2.0, fiberId=4),
    )
    for e in exposures:
        e._targets = targets if e.image_type == "object" else ()
    io.update(fp, "apo", mjd, exposures, SEQUENCES, fibers=True)


def plate_night(fp, mjd=57000):
    exposures = [
        Exposure(observatory="apo", mjd=mjd, exposure=i, imagetyp="Object", plateid=5099, prefix="apR")
        for i in (1, 2)
    ]
    targets = tuple(
        PlateTarget(sdss_id=20 + f, tmass_id=f"2M{f:08d}-0000000", targettype="science",
                    observatory="apo", holeType="apogee", holetype="apogee", ra=1.0, dec=2.0,
                    fiberId=f)
        for f in (1, 2)
    )
    for e in exposures:
        e._targets = targets
    io.update(fp, "apo", mjd, exposures, {"objects": [(1, 2)], "arclamps": [], "missing": []},
              fibers=True)


def make_file(path):
    with h5.File(path, "w") as fp:  # APO only: no raw/lco
        fps_night(fp)
        plate_night(fp)


def test_dump_exposures_single_observatory(tmp_path):
    path, out = str(tmp_path / "a.h5"), str(tmp_path / "e.csv")
    make_file(path)
    result = run_cli(["dump", "exposures", path, out])
    assert result.exit_code == 0
    assert len(Table.read(out)) == 5


def test_dump_fibers_fits_mixed_and_records_night_and_table(tmp_path):
    path, out = str(tmp_path / "a.h5"), str(tmp_path / "f.fits")
    make_file(path)
    result = run_cli(["dump", "fibers", path, out])
    assert result.exit_code == 0
    t = Table.read(out)
    assert len(t) == 6
    rows = sorted(zip(t["mjd"].tolist(), [str(k) for k in t["fiber_map_key"]], t["fiber_id"].tolist()))
    assert rows[:2] == [(57000, "5099", 1), (57000, "5099", 2)]
    assert rows[2] == (60000, "123", 1)
    assert set(str(o) for o in t["observatory"]) == {"apo"}


def test_dump_fibers_hdf5(tmp_path):
    path, out = str(tmp_path / "a.h5"), str(tmp_path / "f.h5")
    make_file(path)
    assert run_cli(["dump", "fibers", path, out]).exit_code == 0
    with h5.File(out, "r") as fp:
        assert len(fp["mjd"]) == 6 and "fiber_map_key" in fp


def test_dump_stars_hdf5_and_no_fake_star(tmp_path):
    path, out = str(tmp_path / "a.h5"), str(tmp_path / "s.h5")
    make_file(path)
    result = run_cli(["dump", "stars", path, out])
    assert result.exit_code == 0, result.output
    with h5.File(out, "r") as fp:
        sdss_ids = sorted(fp["sdss_id"][:].tolist())
        n_exposures = dict(zip(fp["sdss_id"][:].tolist(), fp["n_exposures"][:].tolist()))
    # sdss_id -1 (unmatched fiber) is not a star; sdss_id 0 (solar system) is.
    assert sdss_ids == [0, 11, 21, 22]
    assert n_exposures[11] == 2 and n_exposures[21] == 2
