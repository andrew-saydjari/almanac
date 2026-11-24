"""Arc-lamp sequences and darks (bug H)."""

from almanac import apogee
from almanac.data_models import Exposure


def night(types, dither=12.994):
    return [
        Exposure(observatory="apo", mjd=60000, exposure=i, image_type=t, dithered_pixels=dither,
                 prefix="apR")
        for i, t in enumerate(types, start=1)
    ]


def test_darks_between_lamps_tolerated_leading_trailing_and_alone_not():
    exposures = night([
        "dark", "arclamp", "dark", "arclamp", "dark",   # 1-5: lamps 2-4 with a dark between
        "object",                                         # 6
        "dark", "dark", "arclamp",                        # 7-9: leading darks
        "object",                                         # 10
        "dark", "dark",                                   # 11-12: darks alone
    ])
    assert apogee.get_arclamp_sequences(exposures) == [(2, 4), (9, 9)]


def test_dither_change_splits_and_other_sequences_unchanged():
    exposures = night(["arclamp", "arclamp"]) + [
        Exposure(observatory="apo", mjd=60000, exposure=3, image_type="arclamp",
                 dithered_pixels=13.5, prefix="apR")
    ]
    assert apogee.get_arclamp_sequences(exposures) == [(1, 2), (3, 3)]
    objects = night(["object", "dark", "object", "object"])
    assert apogee.get_sequences(objects, "object", ("image_type",)) == [(1, 1), (3, 4)]
