import numpy as np
from typing import Literal
from typing_extensions import Annotated
from pydantic import BaseModel, Field, model_validator, field_validator

from almanac.data_models.types import *
from almanac.data_models.utils import sanitise_twomass_designation

class FPSTarget(BaseModel):

    """A target that was observed with the SDSS-V Fiber Positioning System."""

    # Target information
    sdss_id: Int64 = Field(default=-1)
    catalogid: Int64 = Field(default=-1)
    twomass_designation: str = Field(default="", alias="tmass_id")
    category: Category = Field(description="Category of the target")
    original_category: Category = Field(
        default="",
        description=(
            "Category reported by the configuration summary file, kept only when "
            "almanac has changed `category` (e.g. to 'offtarget')"
        )
    )
    cadence: str = Field(description="Cadence identifier", default="")
    firstcarton: str = Field(description="Main carton from which this carton was drawn", default="")
    program: str = Field(description="Program for 'firstcarton'", default="")

    # Positioner and hole identifiers
    positioner_id: int = Field(alias='positionerId', description="Positioner identifier", default=-1)
    hole_id: str = Field(alias='holeId', description="Hole ID in which the positioner is sitting", default="")
    hole_type: HoleType = Field(alias="holeType", description="Type of hole", default="fps")
    planned_hole_type: HoleType = Field(alias="holetype", description="Hole type string", default="fps")

    fiber_type: str = Field(alias='fiberType', description="Type of fiber", default="")
    assigned: bool = Field(
        default=False,
        description=(
            "Target is assigned to this fiber in `robostrategy`. If False, no "
            "target assigned for this fiber (likely BOSS instead), and no "
            "targeting information available"
        )
    )

    # Status flags
    on_target: bool = Field(description="Fiber placed on target", default=False)
    disabled: bool = Field(description="Fiber is disabled", default=False)
    valid: bool = Field(description="Converted on-sky coordinates to robot (α,β)", default=False)
    decollided: bool = Field(description="Positioner had to be moved to decollide it", default=False)

    # Position coordinates
    x_wok: float = Field(description="x-coordinate in the wok frame", default=float('NaN'), alias="xwok")
    y_wok: float = Field(description="y-coordinate in the wok frame", default=float('NaN'), alias="ywok")
    z_wok: float = Field(description="z-coordinate in the wok frame", default=float('NaN'), alias="zwok")
    x_focal: float = Field(description="x-coordinate in the focal plane", default=float('NaN'), alias='xFocal')
    y_focal: float = Field(description="y-coordinate in the focal plane", default=float('NaN'), alias='yFocal')

    # Angles
    alpha: float = Field(description="Alpha angle of the positioner arm", default=float('NaN'))
    beta: float = Field(description="Beta angle of the positioner arm", default=float('NaN'))

    # Target coordinates
    ra: float = Field(alias="racat", description="Right Ascension [deg]")
    dec: float = Field(alias="deccat", description="Declination [deg]")
    alt: float = Field(description="Altitude of the fiber on the sky [deg]", default=float('NaN'), alias="alt_observed")
    az: float = Field(description="Azimuth of the fiber on the sky [deg]", default=float('NaN'), alias="az_observed")

    # Wavelength information
    lambda_design: float = Field(default=0.0)
    lambda_eff: float = Field(default=0.0)
    coord_epoch: float = Field(default=0.0)

    # Instrument identifiers
    spectrograph_id: int = Field(description="Spectrograph identifier", alias='spectrographId', default=-1)
    fiber_id: int = Field(description="Fiber identifier", alias='fiberId', default=-1)

    # Position deltas
    delta_ra: float = Field(description="The amount in RA this fiber has been offset", default=float('NaN'))
    delta_dec: float = Field(description="The amount in Dec this fiber has been offset", default=float('NaN'))

    # Target of opportunity
    too: bool = Field(default=False, description="Target of opportunity")
    too_id: int = Field(default=-1)
    too_program: str = Field(default="")

    # Exposure overrides (see `almanac.overrides`; empty when none applies)
    override_id: str = Field(default="", description="Id of the exposure override that changed this fiber (empty if none)")
    override_target: str = Field(default="", description="Name of the object an exposure override says was in this fiber (empty if none)")
    override_kind: str = Field(default="", description="Kind of that object: 'star' (cross-matched as usual) or 'solar_system' (not cross-matched; sdss_id set by the override); empty if none")

    @property
    def expected_to_be_assigned_sdss_id(self) -> bool:
        """ A helper function so we don't try to cross-match sky targets for SDSS IDs. """
        return (self.catalogid > 0
            and not self.category.startswith("sky_")
            and self.category != ""
            and assigns_sdss_id(self.category)
        )

    @property
    def off_target(self) -> bool:
        """
        Whether the positioner was assigned a target but did not reach it.

        When this is true the fiber observed some other patch of sky, so no
        target identity (`sdss_id`) can be asserted for it.
        """
        return bool(self.assigned) and not bool(self.on_target)

    @field_validator("twomass_designation", mode="before")
    def strip_twomass_designation(cls, v) -> str:
        """ Convert a target ID to a standardized designation format. """
        return sanitise_twomass_designation(v)

    @model_validator(mode="before")
    @classmethod
    def relabel_off_target_fibers(cls, data):
        """
        Relabel fibers whose positioner did not reach its assigned target.

        A positioner that was assigned a target (`assigned == 1`) but did not
        reach it (`on_target == 0`) — because it was decollided, disabled, or
        failed to converge — still observes sky, and the configuration summary
        file keeps reporting the assigned target's category and identifiers for
        it. Any `sdss_id` attached to such a fiber is an assertion that the
        assigned star's light is in the fiber, and that assertion is false.

        These fibers are given the `offtarget` category, and are not
        cross-matched to an `sdss_id` (see `almanac.apogee.get_almanac_data`),
        exactly as sky and unplugged fibers are not. The original category is
        kept in `original_category`, and all of the raw configuration summary
        columns (`assigned`, `on_target`, `valid`, `disabled`, `decollided`,
        `catalogid`, `ra`, `dec`, ...) are written unchanged, so these fibers
        remain identifiable for any later serendipitous recovery work.
        """
        if not isinstance(data, dict):
            return data

        # Only relabel when the configuration summary file actually reported
        # both flags. A missing `on_target` column would otherwise default to
        # False and relabel every assigned fiber.
        if "assigned" not in data or "on_target" not in data:
            return data

        category = validate_str(data.get("category", ""))
        if not asserts_target_identity(category):
            return data

        assigned = bool(data["assigned"])
        on_target = bool(data["on_target"])
        if assigned and not on_target:
            data = dict(data)
            data["category"] = OFF_TARGET_CATEGORY
            data["original_category"] = category
            data["sdss_id"] = -1
        return data

    class Config:
        validate_by_name = True
        validate_assignment = True
        arbitrary_types_allowed = True


    @model_validator(mode="after")
    def fix_early_fiber_duplicates(self):
        # From https://github.com/sdss/apogee_drp/blob/4ab6a04e448b279f2514550802b6732693e9847a/python/apogee_drp/utils/plugmap.py#L170-L180
        if self.spectrograph_id == 2:
            if self.positioner_id == 650 and self.fiber_id == 175:
                self.fiber_id = 275
            if self.positioner_id == 880 and self.fiber_id == 176:
                self.fiber_id = 276
            if self.positioner_id == 177 and self.fiber_id == 186:
                self.fiber_id = 286
        return self
