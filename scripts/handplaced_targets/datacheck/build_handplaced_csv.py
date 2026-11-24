"""Build scripts/handplaced_targets/handplaced_2011.csv (committed) from the curated
log inventory, the fiber-level data check and the raw .comments / headers."""
import csv, os, re
from collections import defaultdict
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
RAW = os.environ.get("APOGEE_RAW", "/mnt/ceph/users/sdssv/raw/APOGEE") + "/apo"
inv = list(csv.DictReader(open("notes/inventory_datacheck.csv")))

def comment_lines(mjd):
    out = defaultdict(list)
    for l in open(f"{RAW}/{mjd}/{mjd}.comments", errors="replace"):
        l = l.rstrip("\n").strip()
        m = re.match(r"^([+-]?)0*(\d+)\s*([+]?)\s", l + " ")
        if m:
            if "Silly me" in l:
                continue  # personal remark; its technical content is paraphrased in the note
            out[int(m.group(2))].append(" ".join(l.split()))
    return out

#: Objects whose log name was wrong, by night: the note recorded with each row.
REASSIGNED = {
    55759: {"HD 199799": (
        "the log names HD196944; the raw-header pointing is 46 deg from HD 196944 and 13 arcsec from "
        "HD 199799 (V2141 Cyg, H = 1.7 against 6.6 for HD 196944, consistent with the logged counts), "
        "so the star is HD 199799 (reassigned 2026-11-24)")},
}

cache = {}
rows = []
for r in inv:
    mjd, e = int(r["mjd"]), int(r["exposure"])
    if mjd not in cache:
        cache[mjd] = comment_lines(mjd)
    target = r["target"]
    verdict = r["data_verdict"]
    fiber = int(r["stated_fiber"])
    note = ""
    if r["almanac_n_fibers"] in ("", "0"):
        action = "skip"
        note = "raw header has no PLATEID, so almanac builds no fiber table for this exposure"
    elif verdict == "MATCH":
        action = "retarget_fibers"
    elif verdict.startswith("OTHER_FIBER") and r["hdr_obscmnt"] and f"fib" in r["hdr_obscmnt"].lower() \
            and r["data_brightest_fiber"] in re.findall(r"\d+", r["hdr_obscmnt"].split(",")[-1]):
        action = "retarget_fibers"
        note = (f"log says fiber {fiber}; raw-header OBSCMNT ('{r['hdr_obscmnt']}') and the data check "
                f"say fiber {r['data_brightest_fiber']}: fiber {r['data_brightest_fiber']} used")
        fiber = int(r["data_brightest_fiber"])
    else:
        action = "not_science"
        note = {"NO_DETECTION": "data check: no trace stands out (no star light in any fiber)",
                "MATCH_BUT_SECOND_FIBER": "data check: the stated fiber is brightest but another fiber has a comparable signal"}.get(
            verdict.split("(")[0], f"data check: the brightest trace is fiber {r['data_brightest_fiber']}, the stated fiber "
                                    f"{fiber} is only second (ratio {r['second_over_first']})")
    if target in REASSIGNED.get(mjd, {}):
        note = (note + "; " if note else "") + REASSIGNED[mjd][target]
    rows.append(dict(
        mjd=mjd, exposure=e, plate=r["almanac_plate"], fiber=fiber, target=target.rstrip("?"), action=action,
        fiber_source=r["fiber_source"], data_verdict=verdict, data_brightest_fiber=r["data_brightest_fiber"],
        data_snr_mad=r["snr_mad"], sep_hdr_star_arcsec=r["sep_hdr_star_arcsec"],
        sep_hdr_plate_centre_deg=r["sep_hdr_plate_centre_deg"], hdr_obscmnt=r["hdr_obscmnt"],
        log_lines=" | ".join(cache[mjd].get(e, [])), note=note))
with open(f"{REPO}/scripts/handplaced_targets/handplaced_2011.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
from collections import Counter
print(Counter(r["action"] for r in rows))
