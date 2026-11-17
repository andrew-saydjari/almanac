import numpy as np
from typing import Literal
from typing_extensions import Annotated
from pydantic import BeforeValidator

def validate_np_int64(v):
    if v is None:
        return np.int64(-1)
    if not isinstance(v, np.int64):
        return np.int64(v)
    return v

def validate_int(v):
    if v is None:
        return -1
    return int(v)

def validate_float(v):
    if v is None:
        return float('nan')
    try:
        return float(v)
    except (ValueError, TypeError):
        return float('nan')

def validate_str(v):
    if isinstance(v, bytes):
        return v.decode('utf-8')
    if v is None:
        return ""
    return str(v)

def validate_bool(v):
    if v is None:
        return False
    return bool(v)

Int64 = Annotated[np.int64, BeforeValidator(validate_np_int64)]
Int = Annotated[int, BeforeValidator(validate_int)]
Float = Annotated[float, BeforeValidator(validate_float)]
Str = Annotated[str, BeforeValidator(validate_str)]
Bool = Annotated[bool, BeforeValidator(validate_bool)]


Observatory = Annotated[Literal["apo", "lco"], BeforeValidator(validate_str)]
Prefix = Annotated[Literal["apR", "asR"], BeforeValidator(validate_str)]
Chip = Annotated[Literal["a", "b", "c"], BeforeValidator(validate_str)]

ImageType = Annotated[
    Literal[
        "blackbody",
        "dark",
        "object",
        "domeflat",
        "arclamp",
        "twilightflat",
        "internalflat",
        "quartzflat",
        "missing"
    ],
    BeforeValidator(validate_str)
]

#: Category given to an FPS fiber whose positioner was assigned a target but did
#: not reach it (`assigned == 1` and `on_target == 0`). The fiber observed some
#: other patch of sky, so the assigned target's identity does not apply to it.
OFF_TARGET_CATEGORY = "offtarget"

Category = Annotated[
    Literal[
        "",
        "bonus",
        "science",
        # Stars observed at "veto locations" of manually built FPS designs: an
        # APOGEE fiber placed on a Gaia bright-neighbor star
        # (`veto_location_boss`, carton `ops_gaia_brightneighbors`) or on a
        # bright 2MASS star (`veto_location_apogee`, carton
        # `ops_2mass_psc_brightneighbors`). They are assigned real catalog
        # targets, so they are treated like `science` (cross-matched to an
        # `sdss_id`, see also `IDENTIFIED_STAR_CATEGORIES`) but keep their names.
        "veto_location_boss",
        "veto_location_apogee",
        "sky_apogee",
        "sky_boss",
        "standard_apogee",
        "standard_boss",
        "open_fiber",
        "unplugged",
        OFF_TARGET_CATEGORY,
    ],
    BeforeValidator(validate_str)
]

#: Categories which make no assertion about which target was in the fiber, and so
#: are never cross-matched to an `sdss_id`.
CATEGORIES_WITHOUT_SDSS_ID = ("unplugged", OFF_TARGET_CATEGORY)


def asserts_target_identity(category: str) -> bool:
    """
    Whether a category asserts that a particular target was in the fiber.

    Sky fibers, unplugged fibers, fibers with no category, and fibers already
    marked as off-target make no such assertion, so they are not candidates for
    the off-target relabeling.

    :param category:
        The target category (e.g. "science").
    """
    return (
        category != ""
    and category not in CATEGORIES_WITHOUT_SDSS_ID
    and not category.startswith("sky_")
    )


def assigns_sdss_id(category: str) -> bool:
    """
    Whether a fiber with this category should be assigned an `sdss_id`.

    :param category:
        The target category (e.g. "science").
    """
    return category not in CATEGORIES_WITHOUT_SDSS_ID


HoleType = Annotated[
    Literal[
        "object",
        "coherent_sky",
        "guide",
        "light_trap",
        "alignment",
        "quality",
        "manga",
        "manga_single",
        "manga_alignment",
        "acquisition_center",
        "acquisition_offaxis",
        "apogee",
        "center",
        "trap",
        "boss",
        "apogee_shared",
        "apogee_south",
        "bosshalf",
        "boss_shared",
        "fps",
        "unplugged",
    ],
    BeforeValidator(validate_str)
]

ObjType = Annotated[
    Literal[
        "galaxy",
        "qso",
        "star_bhb",
        "star_carbon",
        "star_brown_dwarf",
        "star_sub_dwarf",
        "star_caty_var",
        "star_red_dwarf",
        "star_white_dwarf",
        "redden_std",
        "spectrophoto_std",
        "hot_std",
        "rosat_a",
        "rosat_b",
        "rosat_c",
        "rosat_d",
        "serendipity_blue",
        "serendipity_first",
        "serendipity_red",
        "serendipity_distant",
        "serendipity_manual",
        "qa",
        "sky",
        "na",
    ],
    BeforeValidator(validate_str)
]

#: Categories whose fiber carries an identified star: the target has a catalog
#: identity, and the fiber is expected to have collected that star's light. These
#: are the fibers that go into star-level summaries (`almanac dump stars`). The
#: veto-location categories are included alongside `science`: they are real stars
#: with catalog identifiers, observed exactly like science targets.
IDENTIFIED_STAR_CATEGORIES = (
    "science",
    "veto_location_boss",
    "veto_location_apogee",
    "standard_apogee",
    "standard_boss",
    "open_fiber",
)
