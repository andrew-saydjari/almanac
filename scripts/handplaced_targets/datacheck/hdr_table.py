"""notes/hdr_retarget_nights.csv: raw-header IMAGETYP/PLATEID/OBSCMNT/RA/DEC/NREAD of every exposure of the nights."""
import csv, glob, os
from astropy.io import fits
RAW = os.environ.get("APOGEE_RAW", "/mnt/ceph/users/sdssv/raw/APOGEE") + "/apo"
NIGHTS = [55699, 55700, 55701, 55702, 55725, 55726, 55727, 55729, 55730, 55731, 55754, 55755, 55757, 55759, 55810, 55811, 55815]
os.makedirs("notes", exist_ok=True)
with open("notes/hdr_retarget_nights.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["mjd", "exposure", "imagetyp", "plateid", "obscmnt", "ra", "dec", "nread"])
    for m in NIGHTS:
        for p in sorted(glob.glob(f"{RAW}/{m}/apR-a-*.apz")):
            try:
                h = fits.getheader(p, 1)
            except Exception:
                continue
            w.writerow([m, int(os.path.basename(p)[-8:-4]), h.get("IMAGETYP"), h.get("PLATEID"),
                        str(h.get("OBSCMNT", "")).strip(), h.get("RA"), h.get("DEC"), h.get("NREAD")])
