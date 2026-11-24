"""
Fiber-level data check for hand-placed-star exposures (read-only on raw data).

For an exposure, build the up-the-ramp signal (last read - first read) of one
chip from the raw apz file, collapse it over a band of columns to a spatial
profile, and find the brightest trace. Map trace rows to fiber numbers with the
trace peaks of a dome flat (or quartz) of the same night: the peaks are sorted
by row and numbered 300..1 (fiber 1 at the top of the detector) -- the
convention is calibrated on APO 55755 (Vesta on fibers 203 / 192 / 295).

Usage: python fibercheck.py MJD FLAT_EXP EXP [EXP ...]
"""
import glob
import os
import sys

import numpy as np
from astropy.io import fits
from scipy.signal import find_peaks

RAW = os.environ.get("APOGEE_RAW", "/mnt/ceph/users/sdssv/raw/APOGEE") + "/apo"
COLS = slice(900, 1150)


def signal(mjd, exp, chip="b"):
    (path,) = glob.glob(f"{RAW}/{mjd}/apR-{chip}-*{exp:04d}.apz")
    with fits.open(path, memmap=False) as f:
        avg = f[1].data.astype(np.float64)
        n = len(f) - 3
        if n < 1:
            return None, 0
        s = np.zeros_like(avg)
        for i in range(3, len(f)):
            s += f[i].data + avg
    return s[:, :2048], n


def profile(mjd, exp, chip="b"):
    s, n = signal(mjd, exp, chip)
    if s is None:
        return None, 0
    p = np.median(s[:, COLS], axis=1)
    p -= np.median(p)
    return p, n


def flat_peaks(mjd, exp, chip="b"):
    p, _ = profile(mjd, exp, chip)
    pk, _ = find_peaks(p, distance=4, height=0.15 * np.percentile(p, 99))
    return pk, p


def number_traces(peaks):
    """
    Fiber number of each flat trace: 300 at the top row, decreasing by one per
    trace, skipping round(gap / spacing) - 1 numbers for every gap wider than
    1.75 trace spacings (dead or unlit fibers; bundle gaps are ~1.4 spacings). Returns (fibers, ok) where ok means the bottom
    trace came out as fiber 1.
    """
    d = np.diff(peaks)
    spacing = np.median(d)
    fibers = [300]
    for g in d:
        r = g / spacing
        fibers.append(fibers[-1] - 1 - (int(round(r)) - 1 if r >= 1.75 else 0))
    fibers = np.array(fibers)
    return fibers, bool(fibers[-1] == 1)


def check(mjd, flat_exp, exps, chip="b"):
    peaks, _ = flat_peaks(mjd, flat_exp, chip)
    fibers, ok = number_traces(peaks)
    out = []
    for e in exps:
        p, n = profile(mjd, e, chip)
        if p is None:
            out.append((mjd, e, n, None, None, None, None, None))
            continue
        # traces are ~3 px wide: sum +-2 rows around each flat peak
        flux = np.array([p[max(r - 2, 0):r + 3].sum() for r in peaks])
        order = np.argsort(flux)[::-1]
        top, second = order[0], order[1]
        fib = int(fibers[top]) if ok else None
        fib2 = int(fibers[second]) if ok else None
        med = np.median(flux)
        mad = np.median(np.abs(flux - med)) * 1.4826 or 1.0
        out.append((mjd, e, n, fib, round(float((flux[top] - med) / mad), 1), fib2,
                    round(float((flux[second] - med) / mad), 1), len(peaks)))
    return out


if __name__ == "__main__":
    mjd, flat = int(sys.argv[1]), int(sys.argv[2])
    exps = [int(x) for x in sys.argv[3:]]
    print("mjd,exposure,n_diff_reads,brightest_fiber,brightest_snr_mad,second_fiber,second_snr_mad,n_flat_traces")
    for row in check(mjd, flat, exps):
        print(",".join("" if v is None else str(v) for v in row))
