"""Join the curated log inventory (notes/curated_from_logs.txt) with the fiber-level data
check (notes/fibercheck_v2.csv), the raw-header pointing table and almanac's fiber-table
availability (notes/plugmap_check.json) into notes/inventory_datacheck.csv."""
import csv, json, math, os
HDR = "notes/hdr_retarget_nights.csv"  # raw-header RA/DEC/PLATEID/OBSCMNT per exposure (hdr_table.py)
fc = {(int(r["mjd"]), int(r["exposure"])): r for r in csv.DictReader(open("notes/fibercheck_v2.csv"))}
pm = json.load(open("notes/plugmap_check.json"))
res = json.load(open("notes/resolved.json"))
hdr = {(r["mjd"], r["exposure"]): r for r in csv.DictReader(open(HDR))}

def sep(a, b, c, d):
    a, b, c, d = map(math.radians, (a, b, c, d))
    return math.degrees(math.acos(min(1, math.sin(b) * math.sin(d) + math.cos(b) * math.cos(d) * math.cos(a - c))))

rows = []
for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "curated_from_logs.txt")):
    if l.startswith("#") or not l.strip():
        continue
    m, e, f, t, src = l.split(); m, e, f = int(m), int(e), int(f)
    target = t.replace("_", " ")
    d = fc.get((m, e), {})
    b = d.get("brightest_fiber", "")
    snr = float(d["brightest_snr_mad"]) if d.get("brightest_snr_mad") else None
    ratio = float(d["ratio_second_to_first"]) if d.get("ratio_second_to_first") else None
    if snr is None: v = "NODATA"
    elif snr < 10: v = "NO_DETECTION"
    elif int(b) == f: v = "MATCH" if ratio < 0.3 else "MATCH_BUT_SECOND_FIBER"
    else: v = f"OTHER_FIBER({b})"
    p = pm.get(str(m), {}).get(str(e))
    h = hdr.get((str(m), str(e)), {})
    s_star = s_plate = float("nan")
    star = res.get(target.rstrip("?"))
    try:
        hra, hdec = float(h["ra"]), float(h["dec"])
        dt = 11.5  # J2000 -> 2011.5
        ra = star["ra"] + (star["pmra"] or 0) / 3.6e6 * dt / math.cos(math.radians(star["dec"]))
        dec = star["dec"] + (star["pmdec"] or 0) / 3.6e6 * dt
        s_star = sep(hra, hdec, ra, dec) * 3600
        s_plate = sep(hra, hdec, *p["centre"]) if p and p["centre"] else float("nan")
    except Exception:
        pass
    rows.append(dict(mjd=m, exposure=e, stated_fiber=f, target=target, fiber_source=src,
                     n_diff_reads=d.get("n_diff_reads", ""), data_brightest_fiber=b, snr_mad=snr,
                     second_fiber=d.get("second_fiber", ""), second_over_first=ratio, data_verdict=v,
                     hdr_ra=h.get("ra"), hdr_dec=h.get("dec"), hdr_obscmnt=h.get("obscmnt"),
                     sep_hdr_star_arcsec=round(s_star, 1), sep_hdr_plate_centre_deg=round(s_plate, 2),
                     almanac_plate=p["plate"] if p else "", almanac_n_fibers=p["n_targets"] if p else ""))
w = csv.DictWriter(open("notes/inventory_datacheck.csv", "w", newline=""), fieldnames=list(rows[0]))
w.writeheader(); w.writerows(rows)
