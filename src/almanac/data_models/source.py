import numpy as np
from pydantic import BaseModel, Field
from almanac.data_models.types import *

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticUseDefault

class Source(BaseModel):
    #> Catalog Cross-Matches (SDSS_ID_To_Catalog)
    sdss_id: Int64 = Field()
    catalogid: Int64 = Field()
    version_id: int = Field(description="Version ID for SDSS ID to catalog mapping")
    lead: str = Field(description="Lead catalog for this SDSS ID")
    allstar_dr17_synspec_rev1: str = Field(default="", description="APOGEE allstar DR17 synspec rev1 ID")
    allwise: Int64 = Field(default=-1, description="AllWISE catalog ID")
    catwise: str = Field(default="", description="CatWISE catalog ID")
    catwise2020: str = Field(default="", description="CatWISE 2020 catalog ID")
    gaia_dr2_source: Int64 = Field(default=-1, description="Gaia DR2 source ID")
    gaia_dr3_source: Int64 = Field(default=-1, description="Gaia DR3 source ID")
    glimpse: Int64 = Field(default=-1, description="GLIMPSE catalog ID")
    guvcat: Int64 = Field(default=-1, description="GALEX GUVcat ID")
    panstarrs1: Int64 = Field(default=-1, description="Pan-STARRS1 catalog ID")
    ps1_g18: Int64 = Field(default=-1, description="PS1 G18 catalog ID")
    sdss_dr13_photoobj: Int64 = Field(default=-1, description="SDSS DR13 photo object ID")
    sdss_dr17_specobj: str = Field(default="", description="SDSS DR17 spec object ID")
    skymapper_dr2: Int64 = Field(default=-1, description="SkyMapper DR2 object ID")
    supercosmos: Int64 = Field(default=-1, description="SuperCOSMOS object ID")
    tic_v8: Int64 = Field(default=-1, description="TESS Input Catalog v8 ID")
    #twomass_id: str = Field(default="", description="Input 2MASS ID to config file (use with caution)")
    twomass_psc: Int64 = Field(default=-1, description="2MASS Point Source Catalog ID")
    tycho2: str = Field(default="", description="Tycho-2 catalog designation")
    unwise: str = Field(default="", description="unWISE object identifier")

    #> SDSS_ID Position (SDSS_ID_Stacked, SDSS_ID_Flat)
    ra: float = Field(default=float('NaN'), description="SDSS_ID right ascension (J2000) [deg]")
    dec: float = Field(default=float('NaN'), description="SDSS_ID declination (J2000) [deg]")
    l: float = Field(default=float('NaN'), description="Galactic longitude [deg]")
    b: float = Field(default=float('NaN'), description="Galactic latitude [deg]")
    healpix: int = Field(default=-1, description="HEALPix pixel (nside=128, RING ordering)")
    n_associated: int = Field(default=-1, description="Number of SDSS_IDs associated with this catalogid")

    #> Gaia DR3 Data
    gaia_source_id: Int64 = Field(default=-1, alias="source_id", description="Gaia DR3 source ID")
    gaia_ra: float = Field(default=float('NaN'), description="Gaia DR3 right ascension [deg]")
    gaia_ra_error: float = Field(default=float('NaN'), description="Gaia DR3 RA error [mas]")
    gaia_dec: float = Field(default=float('NaN'), description="Gaia DR3 declination [deg]")
    gaia_dec_error: float = Field(default=float('NaN'), description="Gaia DR3 Dec error [mas]")
    gaia_parallax: float = Field(default=float('NaN'), alias="parallax", description="Gaia DR3 parallax [mas]")
    gaia_parallax_error: float = Field(default=float('NaN'), alias="parallax_error", description="Gaia DR3 parallax error [mas]")
    gaia_pm: float = Field(default=float('NaN'), alias="pm", description="Gaia DR3 total proper motion [mas/yr]")
    gaia_pmra: float = Field(default=float('NaN'), alias="pmra", description="Gaia DR3 proper motion in RA [mas/yr]")
    gaia_pmra_error: float = Field(default=float('NaN'), alias="pmra_error", description="Gaia DR3 PM RA error [mas/yr]")
    gaia_pmdec: float = Field(default=float('NaN'), alias="pmdec", description="Gaia DR3 proper motion in Dec [mas/yr]")
    gaia_pmdec_error: float = Field(default=float('NaN'), alias="pmdec_error", description="Gaia DR3 PM Dec error [mas/yr]")
    gaia_ruwe: float = Field(default=float('NaN'), alias="ruwe", description="Gaia DR3 RUWE")
    gaia_duplicated_source: bool = Field(default=False, alias="duplicated_source", description="Gaia DR3 duplicated source flag")
    gaia_phot_g_mean_mag: float = Field(default=float('NaN'), alias="phot_g_mean_mag", description="Gaia DR3 G-band mean magnitude [mag]")
    gaia_phot_bp_mean_mag: float = Field(default=float('NaN'), alias="phot_bp_mean_mag", description="Gaia DR3 BP-band mean magnitude [mag]")
    gaia_phot_rp_mean_mag: float = Field(default=float('NaN'), alias="phot_rp_mean_mag", description="Gaia DR3 RP-band mean magnitude [mag]")
    gaia_phot_bp_rp_excess_factor: float = Field(default=float('NaN'), alias="phot_bp_rp_excess_factor", description="Gaia DR3 BP/RP excess factor")
    gaia_radial_velocity: float = Field(default=float('NaN'), alias="radial_velocity", description="Gaia DR3 radial velocity [km/s]")
    gaia_radial_velocity_error: float = Field(default=float('NaN'), alias="radial_velocity_error", description="Gaia DR3 radial velocity error [km/s]")
    gaia_rv_nb_transits: int = Field(default=-1, alias="rv_nb_transits", description="Gaia DR3 number of RV transits")
    gaia_rv_nb_deblended_transits: int = Field(default=-1, alias="rv_nb_deblended_transits", description="Gaia DR3 number of deblended RV transits")
    gaia_rv_visibility_periods_used: int = Field(default=-1, alias="rv_visibility_periods_used", description="Gaia DR3 RV visibility periods used")
    gaia_rv_expected_sig_to_noise: float = Field(default=float('NaN'), alias="rv_expected_sig_to_noise", description="Gaia DR3 expected RV S/N")
    gaia_rv_renormalised_gof: float = Field(default=float('NaN'), alias="rv_renormalised_gof", description="Gaia DR3 RV renormalized GoF")
    gaia_rv_chisq_pvalue: float = Field(default=float('NaN'), alias="rv_chisq_pvalue", description="Gaia DR3 RV chi-squared p-value")
    gaia_rv_time_duration: float = Field(default=float('NaN'), alias="rv_time_duration", description="Gaia DR3 RV time duration [days]")
    gaia_rv_amplitude_robust: float = Field(default=float('NaN'), alias="rv_amplitude_robust", description="Gaia DR3 RV amplitude robust [km/s]")
    gaia_rv_template_teff: float = Field(default=float('NaN'), alias="rv_template_teff", description="Gaia DR3 RV template Teff [K]")
    gaia_rv_template_logg: float = Field(default=float('NaN'), alias="rv_template_logg", description="Gaia DR3 RV template log(g) [dex]")
    gaia_rv_template_fe_h: float = Field(default=float('NaN'), alias="rv_template_fe_h", description="Gaia DR3 RV template [Fe/H] [dex]")
    gaia_rv_atm_param_origin: int = Field(default=-1, alias="rv_atm_param_origin", description="Gaia DR3 RV atmospheric parameter origin")
    gaia_vbroad: float = Field(default=float('NaN'), alias="vbroad", description="Gaia DR3 spectral line broadening [km/s]")
    gaia_vbroad_error: float = Field(default=float('NaN'), alias="vbroad_error", description="Gaia DR3 spectral line broadening error [km/s]")
    gaia_vbroad_nb_transits: int = Field(default=-1, alias="vbroad_nb_transits", description="Gaia DR3 vbroad number of transits")
    gaia_grvs_mag: float = Field(default=float('NaN'), alias="grvs_mag", description="Gaia DR3 G_RVS magnitude [mag]")
    gaia_grvs_mag_error: float = Field(default=float('NaN'), alias="grvs_mag_error", description="Gaia DR3 G_RVS magnitude error [mag]")
    gaia_grvs_mag_nb_transits: int = Field(default=-1, alias="grvs_mag_nb_transits", description="Gaia DR3 G_RVS number of transits")
    gaia_rvs_spec_sig_to_noise: float = Field(default=float('NaN'), alias="rvs_spec_sig_to_noise", description="Gaia DR3 RVS spectrum S/N")
    gaia_teff_gspphot: float = Field(default=float('NaN'), alias="teff_gspphot", description="Gaia DR3 GSP-Phot Teff [K]")
    gaia_logg_gspphot: float = Field(default=float('NaN'), alias="logg_gspphot", description="Gaia DR3 GSP-Phot log(g) [dex]")
    gaia_mh_gspphot: float = Field(default=float('NaN'), alias="mh_gspphot", description="Gaia DR3 GSP-Phot [M/H] [dex]")
    gaia_distance_gspphot: float = Field(default=float('NaN'), alias="distance_gspphot", description="Gaia DR3 GSP-Phot distance [pc]")
    gaia_azero_gspphot: float = Field(default=float('NaN'), alias="azero_gspphot", description="Gaia DR3 GSP-Phot A0 [mag]")
    gaia_ag_gspphot: float = Field(default=float('NaN'), alias="ag_gspphot", description="Gaia DR3 GSP-Phot A_G [mag]")
    gaia_ebpminrp_gspphot: float = Field(default=float('NaN'), alias="ebpminrp_gspphot", description="Gaia DR3 GSP-Phot E(BP-RP) [mag]")

    #> 2MASS Point Source Catalog Data
    twomass_designation: str = Field(default="", description="2MASS PSC designation")
    twomass_j_m: float = Field(default=float('NaN'), alias="j_m", description="2MASS J-band magnitude [mag]")
    twomass_j_cmsig: float = Field(default=float('NaN'), alias="j_cmsig", description="2MASS J-band corrected photometric uncertainty [mag]")
    twomass_j_msigcom: float = Field(default=float('NaN'), alias="j_msigcom", description="2MASS J-band combined uncertainty [mag]")
    twomass_j_snr: float = Field(default=float('NaN'), alias="j_snr", description="2MASS J-band signal-to-noise ratio")
    twomass_h_m: float = Field(default=float('NaN'), alias="h_m", description="2MASS H-band magnitude [mag]")
    twomass_h_cmsig: float = Field(default=float('NaN'), alias="h_cmsig", description="2MASS H-band corrected photometric uncertainty [mag]")
    twomass_h_msigcom: float = Field(default=float('NaN'), alias="h_msigcom", description="2MASS H-band combined uncertainty [mag]")
    twomass_h_snr: float = Field(default=float('NaN'), alias="h_snr", description="2MASS H-band signal-to-noise ratio")
    twomass_k_m: float = Field(default=float('NaN'), alias="k_m", description="2MASS K-band magnitude [mag]")
    twomass_k_cmsig: float = Field(default=float('NaN'), alias="k_cmsig", description="2MASS K-band corrected photometric uncertainty [mag]")
    twomass_k_msigcom: float = Field(default=float('NaN'), alias="k_msigcom", description="2MASS K-band combined uncertainty [mag]")
    twomass_k_snr: float = Field(default=float('NaN'), alias="k_snr", description="2MASS K-band signal-to-noise ratio")
    twomass_ph_qual: str = Field(default="", alias="ph_qual", description="2MASS photometric quality flag")
    twomass_rd_flg: str = Field(default="", alias="rd_flg", description="2MASS read flag")
    twomass_bl_flg: str = Field(default="", alias="bl_flg", description="2MASS blend flag")
    twomass_cc_flg: str = Field(default="", alias="cc_flg", description="2MASS contamination and confusion flag")

    #> unWISE Photometry
    w1_mag: float = Field(default=float('NaN'), description="unWISE W1-band magnitude [Vega mag]")
    e_w1_mag: float = Field(default=float('NaN'), description="Error on unWISE W1-band magnitude [mag]")
    w1_flux: float = Field(default=float('NaN'), alias="flux_w1", description="unWISE W1-band flux [Vega nMgy]")
    w1_dflux: float = Field(default=float('NaN'), alias="dflux_w1", description="Statistical uncertainty in unWISE W1-band flux [Vega nMgy]")
    w1_frac: float = Field(default=float('NaN'), alias="fracflux_w1", description="unWISE W1-band flux fraction from this source (fracflux_w1)")
    w2_mag: float = Field(default=float('NaN'), description="unWISE W2-band magnitude [Vega mag]")
    e_w2_mag: float = Field(default=float('NaN'), description="Error on unWISE W2-band magnitude [mag]")
    w2_flux: float = Field(default=float('NaN'), alias="flux_w2", description="unWISE W2-band flux [Vega nMgy]")
    w2_dflux: float = Field(default=float('NaN'), alias="dflux_w2", description="Statistical uncertainty in unWISE W2-band flux [Vega nMgy]")
    w2_frac: float = Field(default=float('NaN'), alias="fracflux_w2", description="unWISE W2-band flux fraction from this source (fracflux_w2)")
    w1uflags: int = Field(default=0, alias="flags_unwise_w1", description="unWISE W1-band coadd flags (flags_unwise_w1)")
    w2uflags: int = Field(default=0, alias="flags_unwise_w2", description="unWISE W2-band coadd flags (flags_unwise_w2)")
    w1aflags: int = Field(default=0, alias="flags_info_w1", description="Additional unWISE W1-band flags (flags_info_w1)")
    w2aflags: int = Field(default=0, alias="flags_info_w2", description="Additional unWISE W2-band flags (flags_info_w2)")

    #> GLIMPSE Photometry
    mag4_5: float = Field(default=float('NaN'), description="GLIMPSE 4.5um IRAC (Band 2) magnitude [mag]")
    d4_5m: float = Field(default=float('NaN'), description="GLIMPSE 4.5um IRAC (Band 2) 1 sigma error [mag]")
    rms_f4_5: float = Field(default=float('NaN'), description="RMS of detections for 4.5um IRAC (Band 2) [mJy]")
    sqf_4_5: int = Field(default=0, description="GLIMPSE source quality flag for 4.5um IRAC (Band 2)")
    mf4_5: int = Field(default=0, description="GLIMPSE flux calculation method flag for 4.5um IRAC (Band 2)")
    csf: int = Field(default=0, description="GLIMPSE close source flag")

    #> Zhang, Green & Rix (2023) Stellar Parameters
    zgr_teff: float = Field(default=float('NaN'), description="Effective temperature from Zhang, Green & Rix (2023) [K]")
    zgr_e_teff: float = Field(default=float('NaN'), description="Error on effective temperature from Zhang, Green & Rix (2023) [K]")
    zgr_logg: float = Field(default=float('NaN'), description="Surface gravity from Zhang, Green & Rix (2023) [dex]")
    zgr_e_logg: float = Field(default=float('NaN'), description="Error on surface gravity from Zhang, Green & Rix (2023) [dex]")
    zgr_fe_h: float = Field(default=float('NaN'), description="[Fe/H] from Zhang, Green & Rix (2023) [dex]")
    zgr_e_fe_h: float = Field(default=float('NaN'), description="Error on [Fe/H] from Zhang, Green & Rix (2023) [dex]")
    zgr_e: float = Field(default=float('NaN'), description="Extinction from Zhang, Green & Rix (2023) [mag]")
    zgr_e_e: float = Field(default=float('NaN'), description="Error on extinction from Zhang, Green & Rix (2023) [mag]")
    zgr_plx: float = Field(default=float('NaN'), description="Parallax from Zhang, Green & Rix (2023) [mas]")
    zgr_e_plx: float = Field(default=float('NaN'), description="Error on parallax from Zhang, Green & Rix (2023) [mas]")
    zgr_teff_confidence: float = Field(default=float('NaN'), description="Confidence estimate in Zhang, Green & Rix (2023) effective temperature")
    zgr_logg_confidence: float = Field(default=float('NaN'), description="Confidence estimate in Zhang, Green & Rix (2023) surface gravity")
    zgr_fe_h_confidence: float = Field(default=float('NaN'), description="Confidence estimate in Zhang, Green & Rix (2023) [Fe/H]")
    zgr_ln_prior: float = Field(default=float('NaN'), description="Log prior of Zhang, Green & Rix (2023) solution")
    zgr_chi2: float = Field(default=float('NaN'), description="Chi-square value of Zhang, Green & Rix (2023) solution")
    zgr_quality_flags: int = Field(default=0, description="Quality flags from Zhang, Green & Rix (2023)")

    #> Bailer-Jones et al. (2021) Distances
    r_med_geo: float = Field(default=float('NaN'), description="Median geometric distance from Bailer-Jones et al. (2021) [pc]")
    r_lo_geo: float = Field(default=float('NaN'), description="16th percentile of geometric distance from Bailer-Jones et al. (2021) [pc]")
    r_hi_geo: float = Field(default=float('NaN'), description="84th percentile of geometric distance from Bailer-Jones et al. (2021) [pc]")
    r_med_photogeo: float = Field(default=float('NaN'), description="Median photogeometric distance from Bailer-Jones et al. (2021) [pc]")
    r_lo_photogeo: float = Field(default=float('NaN'), description="16th percentile of photogeometric distance from Bailer-Jones et al. (2021) [pc]")
    r_hi_photogeo: float = Field(default=float('NaN'), description="84th percentile of photogeometric distance from Bailer-Jones et al. (2021) [pc]")
    bailer_jones_flags: str = Field(default="", description="Bailer-Jones et al. (2021) distance quality flags")

    #> Reddening
    # These are not populated by `almanac add metadata`; they are derived from
    # other fields in the file (photometry, distances, positions) at a later stage.
    ebv: float = Field(default=float('NaN'), description="E(B-V) reddening [mag]")
    e_ebv: float = Field(default=float('NaN'), description="Error on E(B-V) reddening [mag]")
    ebv_flags: int = Field(
        default=0,
        description=(
            "Reddening flags: 1 upper limit; 2 from Zhang et al. (2023); "
            "4 from Edenhofer et al. (2023); 8 from SFD; 16 from RJCE (GLIMPSE); "
            "32 from RJCE (AllWISE); 64 from Bayestar (2019)"
        ),
    )
    ebv_zhang_2023: float = Field(default=float('NaN'), description="E(B-V) from Zhang et al. (2023) [mag]")
    e_ebv_zhang_2023: float = Field(default=float('NaN'), description="Error on E(B-V) from Zhang et al. (2023) [mag]")
    ebv_sfd: float = Field(default=float('NaN'), description="E(B-V) from SFD [mag]")
    e_ebv_sfd: float = Field(default=float('NaN'), description="Error on E(B-V) from SFD [mag]")
    ebv_rjce_glimpse: float = Field(default=float('NaN'), description="E(B-V) from RJCE using GLIMPSE [mag]")
    e_ebv_rjce_glimpse: float = Field(default=float('NaN'), description="Error on E(B-V) from RJCE using GLIMPSE [mag]")
    ebv_rjce_allwise: float = Field(default=float('NaN'), description="E(B-V) from RJCE using AllWISE [mag]")
    e_ebv_rjce_allwise: float = Field(default=float('NaN'), description="Error on E(B-V) from RJCE using AllWISE [mag]")
    ebv_bayestar_2019: float = Field(default=float('NaN'), description="E(B-V) from Bayestar (2019) [mag]")
    e_ebv_bayestar_2019: float = Field(default=float('NaN'), description="Error on E(B-V) from Bayestar (2019) [mag]")
    ebv_edenhofer_2023: float = Field(default=float('NaN'), description="E(B-V) from Edenhofer et al. (2023) [mag]")
    e_ebv_edenhofer_2023: float = Field(default=float('NaN'), description="Error on E(B-V) from Edenhofer et al. (2023) [mag]")

    #> Gaia DR3 Synthetic Photometry (GSPC)
    c_star: float = Field(default=float('NaN'), description="Gaia DR3 synthetic photometry: C* excess factor")
    u_jkc_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Johnson-Kron-Cousins U magnitude [mag]")
    u_jkc_mag_flag: int = Field(default=0, alias="u_jkc_flag", description="Gaia DR3 synthetic Johnson-Kron-Cousins U magnitude flag")
    b_jkc_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Johnson-Kron-Cousins B magnitude [mag]")
    b_jkc_mag_flag: int = Field(default=0, alias="b_jkc_flag", description="Gaia DR3 synthetic Johnson-Kron-Cousins B magnitude flag")
    v_jkc_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Johnson-Kron-Cousins V magnitude [mag]")
    v_jkc_mag_flag: int = Field(default=0, alias="v_jkc_flag", description="Gaia DR3 synthetic Johnson-Kron-Cousins V magnitude flag")
    r_jkc_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Johnson-Kron-Cousins R magnitude [mag]")
    r_jkc_mag_flag: int = Field(default=0, alias="r_jkc_flag", description="Gaia DR3 synthetic Johnson-Kron-Cousins R magnitude flag")
    i_jkc_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Johnson-Kron-Cousins I magnitude [mag]")
    i_jkc_mag_flag: int = Field(default=0, alias="i_jkc_flag", description="Gaia DR3 synthetic Johnson-Kron-Cousins I magnitude flag")
    u_sdss_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic SDSS u magnitude [mag]")
    u_sdss_mag_flag: int = Field(default=0, alias="u_sdss_flag", description="Gaia DR3 synthetic SDSS u magnitude flag")
    g_sdss_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic SDSS g magnitude [mag]")
    g_sdss_mag_flag: int = Field(default=0, alias="g_sdss_flag", description="Gaia DR3 synthetic SDSS g magnitude flag")
    r_sdss_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic SDSS r magnitude [mag]")
    r_sdss_mag_flag: int = Field(default=0, alias="r_sdss_flag", description="Gaia DR3 synthetic SDSS r magnitude flag")
    i_sdss_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic SDSS i magnitude [mag]")
    i_sdss_mag_flag: int = Field(default=0, alias="i_sdss_flag", description="Gaia DR3 synthetic SDSS i magnitude flag")
    z_sdss_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic SDSS z magnitude [mag]")
    z_sdss_mag_flag: int = Field(default=0, alias="z_sdss_flag", description="Gaia DR3 synthetic SDSS z magnitude flag")
    y_ps1_mag: float = Field(default=float('NaN'), description="Gaia DR3 synthetic Pan-STARRS1 y magnitude [mag]")
    y_ps1_mag_flag: int = Field(default=0, alias="y_ps1_flag", description="Gaia DR3 synthetic Pan-STARRS1 y magnitude flag")

    #> SDSS-IV APOGEE Targeting Flags (DR17 allStar)
    sdss4_apogee_target1_flags: int = Field(default=0, alias="apogee_target1", description="SDSS-IV APOGEE-1 targeting flags (1/2)")
    sdss4_apogee_target2_flags: int = Field(default=0, alias="apogee_target2", description="SDSS-IV APOGEE-1 targeting flags (2/2)")
    sdss4_apogee2_target1_flags: int = Field(default=0, alias="apogee2_target1", description="SDSS-IV APOGEE-2 targeting flags (1/3)")
    sdss4_apogee2_target2_flags: int = Field(default=0, alias="apogee2_target2", description="SDSS-IV APOGEE-2 targeting flags (2/3)")
    sdss4_apogee2_target3_flags: int = Field(default=0, alias="apogee2_target3", description="SDSS-IV APOGEE-2 targeting flags (3/3)")
    sdss4_apogee_member_flags: int = Field(default=0, alias="memberflag", description="SDSS-IV APOGEE likely cluster/galaxy member flags")
    sdss4_apogee_extra_target_flags: int = Field(default=0, alias="extratarg", description="SDSS-IV APOGEE target information flags (EXTRATARG)")

    #> Target of Opportunity (FPS)
    too: bool = Field(default=False, description="Target of opportunity (FPS era)")
    too_id: int = Field(default=-1, description="Target of opportunity ID (FPS era)")
    too_program: str = Field(default="", description="Target of opportunity program (FPS era)")

    #> SDSS-V Targeting Cartons
    sdss5_target_flags: np.ndarray = Field(
        default_factory=lambda: np.zeros((1, 1), dtype=np.uint64),
        description="SDSS-V target flags bitmask array"
    )

    @field_validator("*", mode="before")
    @classmethod
    def none_to_default(cls, v):
        if v is None:
            raise PydanticUseDefault()
        return v

    class Config:
        validate_by_name = True
        validate_assignment = True
        arbitrary_types_allowed = True
