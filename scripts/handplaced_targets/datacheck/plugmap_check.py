"""For each hand-placed night: plate_id, image type, n_targets and plate field centre per object exposure (offline, no DB)."""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
from almanac.apogee import get_almanac_data
out = {}
for mjd in [55699, 55700, 55701, 55702, 55725, 55726, 55727, 55729, 55730, 55731, 55754, 55757, 55759, 55810, 55811, 55815]:
    _, _, exps, seq, _ = get_almanac_data("apo", mjd, fibers=False, overrides=False)
    rows = {}
    for e in exps:
        if e.image_type != "object":
            continue
        try:
            t = e.targets
        except Exception as ex:
            t = ()
        sci = [x for x in t if x.category not in ("sky_apogee", "unplugged", "")]
        ra = np.array([x.ra for x in sci]); dec = np.array([x.dec for x in sci])
        rows[e.exposure] = dict(plate=e.plate_id, n_targets=len(t),
                                centre=[float(np.nanmedian(ra)), float(np.nanmedian(dec))] if len(sci) else None)
    out[mjd] = rows
    print(mjd, {k: (v["plate"], v["n_targets"]) for k, v in rows.items()}, flush=True)
json.dump(out, open("notes/plugmap_check.json", "w"), indent=0)
