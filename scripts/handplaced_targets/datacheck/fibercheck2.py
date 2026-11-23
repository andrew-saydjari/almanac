"""
Fiber-level data check, v2: profiles cached (notes/profiles/), fiber numbering by
matching each night's flat traces to a template numbering (APO 55755 quartz 61,
299 traces, numbered top=300 with gap-aware skipping and validated on the
55755 Vesta/HIP exposures at fibers 203/192/295).

Usage: python fibercheck2.py JOBS_FILE OUT_CSV   (JOBS_FILE lines: mjd flat exp exp ...)
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fibercheck as fc

CACHE = os.path.join("notes", "profiles")  # relative to the working directory
os.makedirs(CACHE, exist_ok=True)


def cached_profile(key):
    mjd, exp = key
    f = os.path.join(CACHE, f"{mjd}_{exp:04d}_b.npz")
    if not os.path.exists(f):
        p, n = fc.profile(mjd, exp)
        np.savez(f, p=p if p is not None else np.zeros(0), n=n)
    d = np.load(f)
    return d["p"], int(d["n"])


def peaks_of(p):
    pk, _ = find_peaks(p, distance=4, height=0.15 * np.percentile(p, 99))
    return pk


def template():
    p, _ = cached_profile((55755, 61))
    pk = peaks_of(p)
    fib, ok = fc.number_traces(pk)
    assert ok and len(pk) == 299
    return pk.astype(float), fib


def map_flat(flat_pk, tpk, tfib):
    best = (-1, 0.0)
    # Trace rows agree to within a few pixels between carts (fiber 300 is at
    # row 24-26 on every flat used here), so the shift is limited to +-3 px
    # (less than half the 7 px trace spacing): no off-by-one ambiguity.
    for s in np.arange(-3, 3.01, 0.25):
        d = np.abs(flat_pk[:, None] - (tpk[None, :] + s)).min(axis=1)
        m = int((d < 1.5).sum())
        if m > best[0]:
            best = (m, s)
    m, s = best
    idx = np.abs(flat_pk[:, None] - (tpk[None, :] + s)).argmin(axis=1)
    return tfib[idx], s, m / len(flat_pk)


def main(jobs_file, out_csv):
    jobs = []
    for line in open(jobs_file):
        if line.strip() and not line.startswith("#"):
            v = [int(x) for x in line.split()]
            jobs.append((v[0], v[1], v[2:]))
    keys = sorted({(m, e) for m, f, es in jobs for e in [f, *es]} | {(55755, 61)})
    with ProcessPoolExecutor(4) as ex:
        list(ex.map(cached_profile, keys))
    tpk, tfib = template()
    rows = ["mjd,exposure,n_diff_reads,flat,flat_traces,flat_shift_px,flat_match_frac,"
            "brightest_fiber,brightest_snr_mad,second_fiber,second_snr_mad,ratio_second_to_first"]
    for mjd, flat, exps in jobs:
        fp, _ = cached_profile((mjd, flat))
        fpk = peaks_of(fp)
        own, ok = fc.number_traces(fpk)
        if ok:  # the flat numbers itself (bottom trace = fiber 1)
            ffib, shift, frac = own, "own", 1.0
        else:   # partial flat: map onto the template
            ffib, shift, frac = map_flat(fpk.astype(float), tpk, tfib)
        for e in exps:
            p, n = cached_profile((mjd, e))
            if p.size == 0:
                rows.append(f"{mjd},{e},{n},{flat},{len(fpk)},{shift},{frac:.3f},,,,,")
                continue
            flux = np.array([p[max(r - 2, 0):r + 3].sum() for r in fpk])
            o = np.argsort(flux)[::-1]
            med = np.median(flux)
            mad = np.median(np.abs(flux - med)) * 1.4826 or 1.0
            rows.append(
                f"{mjd},{e},{n},{flat},{len(fpk)},{shift},{frac:.3f},{ffib[o[0]]},"
                f"{(flux[o[0]] - med) / mad:.1f},{ffib[o[1]]},{(flux[o[1]] - med) / mad:.1f},"
                f"{(flux[o[1]] - med) / max(flux[o[0]] - med, 1e-9):.3f}"
            )
    open(out_csv, "w").write("\n".join(rows) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
