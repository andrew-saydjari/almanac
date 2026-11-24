#!/usr/bin/env python
"""
Regenerate ``src/almanac/etc/handplaced_targets.yaml`` from scratch.

The file holds the targeting information (coordinates and catalogue
identifiers) of the objects that observers placed by hand on single fibers
during the 2011 APOGEE commissioning tests. ``retarget_fibers`` entries in
``exposure_overrides.yaml`` name these objects; almanac writes the targeting
information below into the edited fiber rows and then cross-matches them to
an ``sdss_id`` through its ordinary plate-era path (2MASS designation ->
DR17 allStar APOGEE ID lookup -> 2MASS PSC lookup), exactly like any plugged
fiber. No ``sdss_id`` is stored in the file.

Sources:

* stars: SIMBAD TAP (``basic``, ``ident``, ``allfluxes``): main id, ICRS
  J2000 position and its bibcode, proper motion, parallax, H magnitude and
  the 2MASS / Gaia DR3 / HIP identifiers. If SIMBAD lists no 2MASS
  identifier, the 2MASS PSC (VizieR II/246) is searched around the SIMBAD
  position propagated to epoch 2000.0 (cone radius 5" + |pm| x 3 yr).
* solar-system bodies: no fixed position; only their JPL Horizons
  designation is recorded.

Usage (network access needed; no database access needed)::

    python scripts/handplaced_targets/resolve_targets.py            # print
    python scripts/handplaced_targets/resolve_targets.py --write    # rewrite the YAML

Stars with no 2MASS PSC source (a few of the brightest) are identified by
their SDSS-V ``catalogid`` instead, found with ``--db``: the
``catalogdb.catalog`` row of cross-match version 31 (v1) nearest to the
SIMBAD position, within 2" (+ |pm| x 16 yr for epoch differences). This
needs the database; without ``--db`` such stars are written with
``catalogid: -1`` and a note, and their fibers get no sdss_id.

``--check-db`` additionally prints, for information only, the sdss_id that
almanac's own lookup functions give each 2MASS designation (needs the
database; nothing is written).
"""

import argparse
import csv
import datetime
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGETS_YAML = REPO / "src" / "almanac" / "etc" / "handplaced_targets.yaml"

SIMBAD_TAP = (
    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
    "https://simbad.harvard.edu/simbad/sim-tap/sync",
)
VIZIER_ASU = (
    "https://vizier.cds.unistra.fr/viz-bin/asu-tsv",
    "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv",
)

#: name used in exposure_overrides.yaml -> SIMBAD identifier queried.
#: The name is what the observers wrote (normalised spacing).
STARS = {
    "HD 93288": "HD 93288",
    "HD 122563": "HD 122563",
    "HD 115444": "HD 115444",
    "BD+17 3248": "BD+17 3248",
    "mu Leo": "mu. Leo",
    "AD Leo": "AD Leo",
    "HD 107574": "HD 107574",
    "HR 4422": "HR 4422",
    "HD 107950": "HD 107950",
    "HD 157910": "HD 157910",
    "HD 132737": "HD 132737",
    "Arcturus": "alf Boo",
    "HD 117576": "HD 117576",
    "HD 119617": "HD 119617",
    "HD 120802": "HD 120802",
    "HD 123914": "HD 123914",
    "HD 130322": "HD 130322",
    "HD 132142": "HD 132142",
    "HD 156074": "HD 156074",
    "HD 154653": "HD 154653",
    "HD 156681": "HD 156681",
    "HR 6175": "HR 6175",
    "HD 146051": "HD 146051",
    "HD 227049": "HD 227049",
    "HD 175305": "HD 175305",
    "HD 221170": "HD 221170",
    "GJ 699": "GJ 699",
    "HR 7442": "HR 7442",
    "eta Boo": "eta Boo",
    "alpha Her": "alf01 Her",
    "HD 88609": "HD 88609",
    "beta Cyg": "bet01 Cyg",
    "HD 208527": "HD 208527",
    "30 Her": "30 Her",
    "Antares": "alf Sco A",
    "HD 177724": "HD 177724",
    "beta Peg": "bet Peg",
    "HD 196944": "HD 196944",
    "HD 199799": "HD 199799",
    "HD 9138": "HD 9138",
    "HD 195647": "HD 195647",
    "HD 193342": "HD 193342",
    "2MASS J03322626+4603247": "2MASS J03322626+4603247",
    "beta Oph": "bet Oph",
    "HIP 98456": "HIP 98456",
    "HIP 99987": "HIP 99987",
    "HIP 101226": "HIP 101226",
}

#: Notes copied into the file (identification caveats).
NOTES = {
    "HD 175305": ("The APO 55729 log writes 'HD175035' (a star at RA 283.10, Dec +26.75); the raw-header "
                  "pointing of exposures 58-61 (281.7635, +74.7239) is HD 175305, a metal-poor r+s star, "
                  "14 arcsec away at J2000. Read as a transposition typo."),
    "alpha Her": "Visual binary: alf Her B is 4.6 arcsec away; the fiber (2 arcsec) was on A (SIMBAD 'alf01 Her').",
    "beta Cyg": "Albireo A (bet01 Cyg); B is 35 arcsec away. Raw-header pointing agrees with A.",
    "Antares": "alf Sco A; the B companion is 2.7 arcsec away and may have contributed light.",
    "GJ 699": "Barnard's star: proper motion 10.4 arcsec/yr; the 2011 pointing is ~2 arcmin from the J2000 position.",
    "Arcturus": "Proper motion 2.3 arcsec/yr.",
    "2MASS J03322626+4603247": "SIMBAD: 36 Per.",
    "HD 199799": ("APO 55759 exposures 59-68: the log names HD196944, but the raw-header pointing "
                  "(314.47, +44.79) is 46 deg from HD 196944 and 13-15 arcsec from HD 199799 "
                  "(V2141 Cyg), which is also ~5 mag brighter in H, as the logged counts suggest. "
                  "Reassigned to HD 199799 (AKS 2026-11-24)."),
    "HIP 98456": "Same star as HD 227049.",
    "HIP 99987": "Same star as HD 193342.",
    "HIP 101226": "Same star as HD 195647.",
}

#: Solar-system bodies: name -> JPL Horizons designation.
SOLAR_SYSTEM = {
    "VESTA": {"jpl_horizons_id": "4", "full_name": "(4) Vesta"},
}


def _post(urls, data, timeout=60):
    last = None
    for url in urls:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode()
            if text.lstrip().startswith("<"):
                raise RuntimeError(f"{url} returned HTML (service unavailable?)")
            return url, text
        except Exception as e:  # try the next mirror
            last = e
    raise RuntimeError(f"all mirrors failed: {last}")


def simbad(adql):
    url, text = _post(SIMBAD_TAP, {"request": "doQuery", "lang": "adql", "format": "csv", "query": adql})
    return url, list(csv.DictReader(io.StringIO(text)))


def _q(s):
    return "'" + s.replace("'", "''") + "'"


def resolve_star(name, ident):
    url, rows = simbad(
        "SELECT b.oid, b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.coo_bibcode, "
        "b.sp_type, f.H FROM basic AS b JOIN ident AS i ON i.oidref = b.oid "
        f"LEFT JOIN allfluxes AS f ON f.oidref = b.oid WHERE i.id = {_q(ident)}"
    )
    if len(rows) != 1:
        raise RuntimeError(f"{name}: SIMBAD returned {len(rows)} rows for {ident!r}")
    b = rows[0]
    _, ids = simbad(f"SELECT id FROM ident WHERE oidref = {b['oid']}")
    ids = [r["id"] for r in ids]

    def pick(prefix):
        hits = sorted(i for i in ids if i.startswith(prefix))
        return hits[0][len(prefix):].strip() if hits else ""

    def num(v):
        return float(v) if v not in ("", None) else None

    out = {
        "kind": "star",
        "simbad_main_id": " ".join(b["main_id"].split()),
        "simbad_query": ident,
        "ra": round(float(b["ra"]), 7),
        "dec": round(float(b["dec"]), 7),
        "coord_bibcode": b["coo_bibcode"],
        "pmra": num(b["pmra"]),
        "pmdec": num(b["pmdec"]),
        "parallax": num(b["plx_value"]),
        "sp_type": b["sp_type"],
        "h_mag_simbad": num(b["H"]),
        "twomass_designation": pick("2MASS J"),
        "gaia_dr3_source_id": pick("Gaia DR3 "),
        "hip": pick("HIP "),
        "source": f"SIMBAD TAP ({urllib.parse.urlparse(url).netloc})",
    }
    if not out["twomass_designation"]:
        out.update(twomass_cone(out))
    return out


def twomass_cone(star):
    """Nearest 2MASS PSC source to the SIMBAD position propagated to epoch 2000.0."""
    import math
    pm = math.hypot(star["pmra"] or 0.0, star["pmdec"] or 0.0) / 1000.0  # arcsec / yr
    radius = 5.0 + 3.0 * pm
    params = {
        "-source": "II/246/out", "-c": f"{star['ra']} {star['dec']:+}",
        "-c.rs": f"{radius:.1f}", "-out": "2MASS,RAJ2000,DEJ2000,Hmag,_r", "-sort": "_r",
        "-out.max": "1",
    }
    url, text = _post(VIZIER_ASU, params)
    rows = [l.split("\t") for l in text.splitlines() if l and not l.startswith("#")]
    rows = [r for r in rows if len(r) >= 5 and len(r[0].strip()) == 16 and r[0].strip()[8] in "+-"]
    if not rows:
        return {"twomass_designation": "", "twomass_note": f"no 2MASS PSC source within {radius:.1f} arcsec"}
    r = rows[0]
    return {
        "twomass_designation": r[0].strip(),
        "twomass_note": f"nearest 2MASS PSC source ({float(r[4]):.2f} arcsec, VizieR II/246); not listed by SIMBAD",
    }


CATALOG_VERSION_ID = 31


def catalogid_cone(star):
    """Nearest catalogdb.catalog (version 31) row to the SIMBAD position."""
    import math
    sys.path.insert(0, str(REPO / "src"))
    from almanac.database import catalogdb
    pm = math.hypot(star["pmra"] or 0.0, star["pmdec"] or 0.0) / 1000.0
    radius = 2.0 + 16.0 * pm
    rows = catalogdb.database.execute_sql(
        "SELECT catalogid, lead, q3c_dist(ra, dec, %s, %s) * 3600 AS d FROM catalogdb.catalog "
        "WHERE version_id = %s AND q3c_radial_query(ra, dec, %s, %s, %s) ORDER BY d LIMIT 1",
        (star["ra"], star["dec"], CATALOG_VERSION_ID, star["ra"], star["dec"], radius / 3600.0),
    ).fetchall()
    if not rows:
        return {"catalogid": -1, "catalogid_note": f"no version-{CATALOG_VERSION_ID} catalog row within {radius:.1f} arcsec"}
    cid, lead, d = rows[0]
    return {
        "catalogid": int(cid),
        "catalogid_note": (f"catalogdb.catalog version_id {CATALOG_VERSION_ID} row nearest the SIMBAD "
                           f"position ({d:.2f} arcsec, lead {lead}); used because no 2MASS PSC source"),
    }


def check_db(targets):
    sys.path.insert(0, str(REPO / "src"))
    from almanac.apogee import get_apogee_id_lookup, twomass_sdss_id_lookup
    from almanac.data_models.utils import sanitise_twomass_designation
    keys = {n: sanitise_twomass_designation(t["twomass_designation"])
            for n, t in targets.items() if t.get("twomass_designation")}
    a = get_apogee_id_lookup(set(keys.values()))
    rest = {k for k in keys.values() if k not in a}
    b = twomass_sdss_id_lookup(rest) if rest else {}
    for n, k in keys.items():
        print(f"{n:28s} {k:18s} sdss_id={a.get(k, b.get(k, -1))} via={'apogee_id' if k in a else ('twomass_psc' if k in b else '-')}")


def render(targets):
    import yaml
    today = datetime.date.today().isoformat()
    head = [
        "# Targeting information for objects placed by hand on single fibers",
        "# (2011 APOGEE commissioning tests). Referenced by name from the",
        "# `fibers:` mapping of `retarget_fibers` entries in exposure_overrides.yaml.",
        "#",
        "# GENERATED by scripts/handplaced_targets/resolve_targets.py --write",
        f"# on {today}; do not edit by hand, edit the script and regenerate.",
        "#",
        "# almanac copies a star's twomass_designation / ra / dec into the edited",
        "# fiber row (category `science`) and then cross-matches it to an sdss_id",
        "# through the ordinary plate-era path, like any plugged fiber. No",
        "# sdss_id is stored here. ra/dec are ICRS J2000 (SIMBAD, epoch J2000).",
        "#",
        "# A solar-system body has no fixed position and no catalogue entry: its",
        "# fiber is category `science` (so it is reduced) with no 2MASS designation,",
        "# ra/dec = the raw-header RA/DEC of that exposure (each such exposure gets",
        "# its own fiber table), and sdss_id = solar_system_sdss_id (below); it is",
        "# not cross-matched, and `almanac add metadata` writes a fixed /meta row",
        "# for it (lead 'solar_system', everything else NaN / -1) without a query.",
        "#",
        "# solar_system_sdss_id: SETTING (accepted by AKS 2026-11-24 for now), NOT A",
        "# VERIFIED SDSS CONVENTION. On",
        "# 2026-11-23 the SDSS database had no sdss_id 0 (the smallest is 1,",
        "# and sdss_id 1 is an ordinary star at RA 312.73, Dec -3.29), and no",
        "# reserved 'Sun' sdss_id was found in almanac, ApogeeReduction.jl or",
        "# arMADGICS.jl. 0 is used because it collides with no catalogue",
        "# source.",
    ]
    doc = {"version": 1, "solar_system_sdss_id": 0, "targets": targets}
    return "\n".join(head) + "\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help=f"rewrite {TARGETS_YAML}")
    p.add_argument("--db", action="store_true", help="find catalogids for stars without a 2MASS designation")
    p.add_argument("--check-db", action="store_true", help="print the sdss_id almanac's lookups give (information only)")
    p.add_argument("--json", help="also dump the resolved records to this JSON file")
    a = p.parse_args()

    targets = {}
    for name, ident in STARS.items():
        targets[name] = resolve_star(name, ident)
        if name in NOTES:
            targets[name]["note"] = NOTES[name]
        if not targets[name]["twomass_designation"]:
            targets[name].update(catalogid_cone(targets[name]) if a.db else
                                 {"catalogid": -1, "catalogid_note": "not resolved (run with --db)"})
        print(f"{name:28s} {targets[name]['simbad_main_id']:24s} {targets[name]['ra']:11.6f} "
              f"{targets[name]['dec']:+11.6f} 2MASS J{targets[name]['twomass_designation']} "
              f"H={targets[name]['h_mag_simbad']}", file=sys.stderr)
    for name, rec in SOLAR_SYSTEM.items():
        targets[name] = {"kind": "solar_system", **rec, "source": "JPL Horizons designation"}

    text = render(targets)
    if a.json:
        Path(a.json).write_text(json.dumps(targets, indent=1))
    if a.write:
        TARGETS_YAML.write_text(text)
        print(f"wrote {TARGETS_YAML}", file=sys.stderr)
    else:
        print(text)
    if a.check_db:
        check_db({n: t for n, t in targets.items() if t["kind"] == "star"})


if __name__ == "__main__":
    main()
