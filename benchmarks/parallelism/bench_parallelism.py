#!/usr/bin/env python3
"""
Time `almanac` runs over a small grid of --processes x --query-workers and
record the peak number of database connections the run held at once.

Self-contained: standard library only (Linux; connections are counted from
/proc, without querying the database). Runs the `almanac` executable given
with --almanac, always with --fibers and cross-matching on (never
--no-x-match). See README.md in this directory.

Example (gentle):
    python bench_parallelism.py --almanac .venv/bin/almanac --obs apo \\
        --daily-mjd 60855 --bulk-mjd-start 60850 --bulk-mjd-end 60853 \\
        --processes 1,2,4 --query-workers 1,2,4 --outdir bench_out
"""

import argparse
import csv
import datetime
import json
import os
import socket
import subprocess
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# Counting database connections of a process tree from /proc
# --------------------------------------------------------------------------- #


def _children_map():
    children = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                stat = f.read()
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(name))
    return children


def _tree(pid):
    children = _children_map()
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(children.get(p, []))
    return out


def _socket_inodes(pids):
    inodes = set()
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if link.startswith("socket:["):
                inodes.add(link[8:-1])
    return inodes


def _db_socket_inodes(db_port):
    """Inodes of ESTABLISHED TCP sockets whose remote port is db_port."""
    found = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                next(f)
                for line in f:
                    parts = line.split()
                    remote, state, inode = parts[2], parts[3], parts[9]
                    if state == "01" and int(remote.rsplit(":", 1)[1], 16) == db_port:
                        found.add(inode)
        except OSError:
            continue
    return found


def count_db_connections(pid, db_port):
    return len(_socket_inodes(_tree(pid)) & _db_socket_inodes(db_port))


class ConnectionSampler(threading.Thread):
    def __init__(self, pid, db_port, interval=0.25):
        super().__init__(daemon=True)
        self.pid, self.db_port, self.interval = pid, db_port, interval
        self.peak = 0
        self.samples = 0
        self.total = 0
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            n = count_db_connections(self.pid, self.db_port)
            self.peak = max(self.peak, n)
            self.samples += 1
            self.total += n
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()
        self.join()

    @property
    def mean(self):
        return self.total / self.samples if self.samples else 0.0


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


def _ints(text):
    return [int(v) for v in str(text).split(",") if v.strip()]


def _config_db_port():
    path = os.path.expanduser("~/.almanac/config.yaml")
    try:
        with open(path) as f:
            in_sdssdb = False
            for line in f:
                if line.startswith("sdssdb:"):
                    in_sdssdb = True
                elif in_sdssdb and line.strip().startswith("port:"):
                    return int(line.split(":", 1)[1].split("#")[0])
                elif line and not line.startswith(" "):
                    in_sdssdb = False
    except OSError:
        pass
    return 5432


def summarize_output(path):
    """Nights written and sdss_id coverage, read with h5py if available."""
    try:
        import h5py
        import numpy as np
    except ImportError:
        return {}
    out = dict(n_nights=0, n_sdss_id_pos=0, n_fibers=0)
    try:
        with h5py.File(path, "r") as fp:
            for obs in fp.get("raw", {}):
                for mjd in fp[f"raw/{obs}"]:
                    out["n_nights"] += 1
                    fibers = fp.get(f"raw/{obs}/{mjd}/fibers")
                    if fibers is None:
                        continue
                    for config in fibers:
                        if "sdss_id" in fibers[config]:
                            s = fibers[config]["sdss_id"][:]
                            out["n_fibers"] += int(s.size)
                            out["n_sdss_id_pos"] += int(np.count_nonzero(s > 0))
    except OSError as e:
        out["error"] = str(e)
    return out


def run_one(args, label, mjd_args, processes, query_workers, outdir):
    tag = f"{label}_p{processes}_q{query_workers}"
    output = os.path.join(outdir, f"{tag}.h5")
    log = os.path.join(outdir, f"{tag}.log")
    if os.path.exists(output):
        os.remove(output)
    cmd = [args.almanac, "-p", str(processes), "--query-workers", str(query_workers),
           f"--{args.obs}", "--fibers", *mjd_args, "-O", output]
    env = dict(os.environ)
    env.pop("SLURM_CPUS_ON_NODE", None)  # the grid sets -p explicitly
    t0 = time.time()
    with open(log, "w") as lf:
        lf.write(" ".join(cmd) + "\n")
        lf.flush()
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env)
        sampler = ConnectionSampler(proc.pid, args.db_port, args.sample_interval)
        sampler.start()
        try:
            code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = "timeout"
        sampler.stop()
    wall = time.time() - t0
    row = dict(
        case=label, processes=processes, query_workers=query_workers,
        wall_s=round(wall, 1), exit=code,
        peak_db_connections=sampler.peak, mean_db_connections=round(sampler.mean, 2),
        start_utc=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc).isoformat(timespec="seconds"),
        command=" ".join(cmd[1:]),
    )
    row.update(summarize_output(output))
    if not args.keep_outputs and os.path.exists(output):
        os.remove(output)
    print(json.dumps(row), flush=True)
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--almanac", required=True, help="almanac executable")
    ap.add_argument("--obs", default="apo", choices=("apo", "lco"))
    ap.add_argument("--daily-mjd", type=int, help="one night (daily case)")
    ap.add_argument("--bulk-mjd-start", type=int, help="first night of the bulk piece")
    ap.add_argument("--bulk-mjd-end", type=int, help="last night of the bulk piece")
    ap.add_argument("--processes", default="1,2,4", help="comma list (bulk case)")
    ap.add_argument("--query-workers", default="1,2,4", help="comma list (bulk case)")
    ap.add_argument("--daily-repeats", type=int, default=2,
                    help="daily runs (one night always runs in one process; repeats show cache effects)")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip the untimed warm-up run of the bulk piece")
    ap.add_argument("--db-port", type=int, default=None,
                    help="database port the client connects to (default: ~/.almanac/config.yaml)")
    ap.add_argument("--sample-interval", type=float, default=0.25)
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--max-processes", type=int, default=8, help="refuse larger grids (be gentle)")
    ap.add_argument("--max-query-workers", type=int, default=8)
    ap.add_argument("--keep-outputs", action="store_true")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args(argv)
    args.db_port = args.db_port or _config_db_port()

    processes, query_workers = _ints(args.processes), _ints(args.query_workers)
    if max(processes) > args.max_processes or max(query_workers) > args.max_query_workers:
        ap.error("grid exceeds --max-processes/--max-query-workers")
    os.makedirs(args.outdir, exist_ok=True)

    meta = dict(host=socket.gethostname(), started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                db_port=args.db_port, argv=sys.argv[1:])
    rows = []
    if args.daily_mjd is not None:
        for i in range(args.daily_repeats):
            rows.append(run_one(args, f"daily{i + 1}", ["--mjd", str(args.daily_mjd)], 1, 1, args.outdir))
    if args.bulk_mjd_start is not None and args.bulk_mjd_end is not None:
        bulk = ["--mjd-start", str(args.bulk_mjd_start), "--mjd-end", str(args.bulk_mjd_end)]
        if not args.no_warmup:
            rows.append(run_one(args, "bulk_warmup", bulk, min(processes), min(query_workers), args.outdir))
        for p in processes:
            for q in query_workers:
                if q > p and p > 1:
                    continue  # q > p behaves as q == p (each worker one connection)
                if p == 1 and q != min(query_workers):
                    continue  # one process: one connection whatever q is
                rows.append(run_one(args, "bulk", bulk, p, q, args.outdir))

    meta["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(args.outdir, "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.outdir, "results.json"), "w") as f:
        json.dump(dict(meta=meta, rows=rows), f, indent=1)
    cols = ["case", "processes", "query_workers", "wall_s", "peak_db_connections",
            "mean_db_connections", "n_nights", "n_sdss_id_pos", "n_fibers", "exit"]
    with open(os.path.join(args.outdir, "results.md"), "w") as f:
        f.write(f"host {meta['host']}, {meta['started_utc']} to {meta['finished_utc']} (UTC)\n\n")
        f.write("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n")
        for r in rows:
            f.write("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n")
    print(open(os.path.join(args.outdir, "results.md")).read())


if __name__ == "__main__":
    main()
