"""
Regression tests for Exposure.config_summary_path flavor preference.

Background: a BOSS-led configuration observed without an FVC loop correction
produces no confSummaryF/FS, but it does get a confSummaryS -- sky assignment
runs regardless of the FVC loop. The chain used to be ("FS", ""), which skipped
"S" and fell all the way to the base confSummary. The base file carries no
sky_apogee rows, so every APOGEE fiber came back with an empty category and
downstream pipelines dropped the exposure entirely.
"""
import os

import pytest

from almanac import config
from almanac.data_models.exposure import Exposure


def _summary_dir(root, observatory, config_id):
    return os.path.join(
        root,
        observatory,
        "summary_files",
        f"{str(config_id)[:-3].zfill(3)}XXX",
        f"{str(config_id)[:-2].zfill(4)}XX",
    )


@pytest.fixture
def sdsscore(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "sdsscore_dir", str(tmp_path))
    return tmp_path


def _make(sdsscore, observatory, config_id, flavors):
    d = _summary_dir(sdsscore, observatory, config_id)
    os.makedirs(d, exist_ok=True)
    for flavor in flavors:
        open(os.path.join(d, f"confSummary{flavor}-{config_id}.par"), "w").close()
    return d


def _exposure(observatory, config_id):
    exp = Exposure.__new__(Exposure)
    object.__setattr__(exp, "observatory", observatory)
    object.__setattr__(exp, "config_id", config_id)
    return exp


@pytest.mark.parametrize(
    "present,expected",
    [
        (("FS", "S", "F", ""), "confSummaryFS-5525.par"),  # everything -> FS
        (("S", ""), "confSummaryS-5525.par"),              # no FVC loop -> S, NOT base
        (("FS", ""), "confSummaryFS-5525.par"),
        (("",), "confSummary-5525.par"),                   # last resort
        (("S",), "confSummaryS-5525.par"),
    ],
)
def test_flavor_preference_order(sdsscore, present, expected):
    _make(sdsscore, "apo", 5525, present)
    assert os.path.basename(_exposure("apo", 5525).config_summary_path) == expected


def test_S_is_preferred_over_base(sdsscore):
    """The specific regression: S present, no FS -> must not fall back to base."""
    _make(sdsscore, "apo", 5532, ("S", ""))
    path = _exposure("apo", 5532).config_summary_path
    assert path.endswith("confSummaryS-5532.par")


def test_F_alone_does_not_win_over_base_sky_assignment(sdsscore):
    """
    "F" is FVC-corrected but has no sky assignment, so it is deliberately not in
    the chain. With only F and base present, the base file is returned.
    """
    _make(sdsscore, "apo", 5540, ("F", ""))
    assert _exposure("apo", 5540).config_summary_path.endswith("confSummary-5540.par")


def test_missing_raises(sdsscore):
    _make(sdsscore, "apo", 5599, ())
    with pytest.raises(FileNotFoundError):
        _exposure("apo", 5599).config_summary_path
