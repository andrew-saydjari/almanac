#!/usr/bin/env python
"""
Mechanical documentation audit for almanac.

Generates the ground truth from the code of a checkout and diffs the user-facing
documentation against it, so the audit can be re-run after any change:

    cd <almanac checkout>
    uv run python docs/audit_docs.py --repo . --out <folder>

Ground truth (written to <out>/ground_truth.json and ground_truth.md):

* the click command tree: every command and subcommand, every option (all
  spellings, type, default, flag/count, hidden) and every `--help` text;
* the Pydantic models written to HDF5 (`Exposure`, `FPSTarget`, `PlateTarget`,
  `Source`, `ExtendedSource`): fields, aliases, computed fields;
* the configuration dataclass (`almanac.config.Config`): keys and defaults;
* a small almanac file built in a temporary directory with the real writers
  (`io.update`, `io.write_night_attrs`, `io.write_missing_exposures`) from
  synthetic exposures (one FPS night, one plate night with an exposure
  override and a hand-placed solar-system body), then decorated in place
  with `metadata.build_metadata` (fake catalog query, no database), and a
  copy built with `--extended`: every group, dataset (dtype, shape) and
  attribute name.

Checks (written to <out>/audit_report.md; exit status 1 if any ERROR):

1. every `almanac ...` command line in a code block, and every inline
   `almanac ...` code span, of README.md, docs/*.md, docs/index.rst resolves
   to an existing command, and each option it uses exists for that command
   (main-command options after a subcommand are an error);
2. every inline `--option` code span names an option of some command;
3. every key in `almanac config get|set KEY` exists, and the configuration
   reference (docs/configuration.md `#### \`key\`` headings) lists exactly the
   dataclass keys;
4. the field tables of docs/data-formats.md (Exposure / FPSTarget /
   PlateTarget sections) list exactly the model fields (incl. computed ones),
   with the right aliases;
5. the attribute / dataset tables of docs/data-formats.md marked with
   `<!-- audit:table NAME -->` list exactly what the synthetic file holds
   (night attributes, /missing_exposures datasets, /meta attributes,
   side groups);
6. lint: no public-web URL, no cluster-internal `/mnt/` or `/uufs/` path, no
   "fibre" outside the `--fibres` alias, no `--no-x-match` in a code-block
   example unless the line is marked `# not recommended`, the documented
   version equals the package version.
"""

import argparse
import contextlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

import click
import h5py as h5
import numpy as np

DOC_GLOBS = ("README.md", "docs/*.md", "docs/index.rst")

problems = []  # (level, where, message)


def problem(level, where, message):
    problems.append((level, where, message))


# --------------------------------------------------------------------------- #
# Ground truth: CLI
# --------------------------------------------------------------------------- #


def cli_tree():
    from almanac.cli import main

    out = {}

    def walk(cmd, path):
        params = []
        for p in cmd.params:
            if isinstance(p, click.Option):
                params.append(dict(
                    kind="option", name=p.name, opts=list(p.opts) + list(p.secondary_opts),
                    type=str(p.type), default=None if callable(p.default) else repr(p.default),
                    is_flag=bool(p.is_flag), count=bool(p.count), hidden=bool(p.hidden),
                    help=p.help or "",
                ))
            else:
                params.append(dict(kind="argument", name=p.name, nargs=p.nargs,
                                   required=bool(p.required), type=str(p.type)))
        ctx = click.Context(cmd, info_name=" ".join(path))
        out[" ".join(path)] = dict(
            group=isinstance(cmd, click.Group), hidden=bool(getattr(cmd, "hidden", False)),
            help=cmd.get_help(ctx), params=params,
            subcommands=sorted(getattr(cmd, "commands", {}).keys()),
        )
        for name, sub in sorted(getattr(cmd, "commands", {}).items()):
            walk(sub, path + [name])

    walk(main, ["almanac"])
    return out


def option_set(entry):
    s = set()
    for p in entry["params"]:
        if p["kind"] == "option":
            s.update(p["opts"])
    s.add("--help")
    return s


# --------------------------------------------------------------------------- #
# Ground truth: models and config
# --------------------------------------------------------------------------- #


def models():
    from almanac.data_models import Exposure
    from almanac.data_models.fps import FPSTarget
    from almanac.data_models.plate import PlateTarget
    from almanac.data_models.source import Source, ExtendedSource, EXTENDED_COLUMN_GROUPS

    out = {}
    for M in (Exposure, FPSTarget, PlateTarget, Source, ExtendedSource):
        out[M.__name__] = dict(
            fields={n: dict(alias=f.alias, annotation=str(f.annotation),
                            default=repr(f.default) if not f.is_required() else None)
                    for n, f in M.model_fields.items()},
            computed=list(M.model_computed_fields),
        )
    out["EXTENDED_COLUMN_GROUPS"] = {k: list(v) for k, v in EXTENDED_COLUMN_GROUPS.items()}
    return out


def config_keys():
    from dataclasses import fields, is_dataclass
    from almanac.config import Config

    keys = {}

    def walk(cls, prefix=""):
        inst = cls()
        for f in fields(cls):
            v = getattr(inst, f.name)
            if is_dataclass(v):
                walk(type(v), prefix + f.name + ".")
            else:
                keys[prefix + f.name] = repr(v)

    walk(Config)
    return keys


# --------------------------------------------------------------------------- #
# Ground truth: a synthetic almanac file written by the real writers
# --------------------------------------------------------------------------- #


def h5_listing(path):
    listing = {}

    def visit(name, obj):
        entry = dict(attrs=sorted(obj.attrs.keys()))
        if isinstance(obj, h5.Dataset):
            entry.update(kind="dataset", dtype=str(obj.dtype), shape=list(obj.shape))
        else:
            entry.update(kind="group")
        listing["/" + name] = entry

    with h5.File(path, "r") as fp:
        listing["/"] = dict(kind="group", attrs=sorted(fp.attrs.keys()))
        fp.visititems(visit)
    return listing


def build_synthetic(tmp):
    from almanac import apogee, io, metadata
    from almanac.data_models import Exposure
    from almanac.data_models.fps import FPSTarget
    from almanac.data_models.plate import PlateTarget
    from almanac.overrides import overrides_attrs, override_table_id

    def exp(obs, mjd, n, image_type, **kw):
        return Exposure(observatory=obs, mjd=mjd, exposure=n, image_type=image_type, **kw)

    # FPS night: object sequence 1-2 (config 5000), arclamp, missing, dark.
    fps = [
        exp("apo", 60000, 1, "object", config_id=5000, field_id=100, design_id=7),
        exp("apo", 60000, 2, "object", config_id=5000, field_id=100, design_id=7),
        exp("apo", 60000, 3, "arclamp", dithered_pixels=0.5),
        exp("apo", 60000, 4, "missing"),
        exp("apo", 60000, 5, "dark"),
    ]
    fps[0]._targets = (
        FPSTarget(category="science", ra=1.0, dec=2.0, fiberId=1, sdss_id=11, catalogid=27021597),
        FPSTarget(category="offtarget", original_category="science", ra=1.0, dec=2.0, fiberId=2),
        FPSTarget(category="sky_apogee", ra=1.0, dec=2.0, fiberId=3),
    )
    # Plate night: exposure 1 on plate 8000; exposure 2 edited by an override
    # (synthetic table id, hand-placed solar-system body on fiber 1).
    plate = [
        exp("apo", 57000, 1, "object", plate_id=8000, field_id=200, name="8000-56999-01"),
        exp("apo", 57000, 2, "object", plate_id=8000, field_id=200, name="8000-56999-01"),
    ]
    common = dict(observatory="apo", holeType="object", holetype="object", ra=3.0, dec=4.0)
    plate[0]._targets = tuple(
        PlateTarget(category="science", fiberId=i, sdss_id=20 + i, **common) for i in (1, 2)
    )
    synthetic = override_table_id(57000, 1)
    plate[1]._original_plate_id = 8000
    plate[1].plate_id = synthetic
    plate[1].override_ids = "example-entry"
    plate[1].override_actions = "retarget_fibers(1=VESTA;others=offtarget)"
    plate[1]._targets = (
        PlateTarget(category="science", original_category="science", override_id="example-entry",
                    override_target="VESTA", override_kind="solar_system", fiberId=1, sdss_id=0,
                    **common),
        PlateTarget(category="offtarget", original_category="science", override_id="example-entry",
                    fiberId=2, **common),
    )

    source = dict(source="materialized_view", view="catalogdb.mv_apogee_id_to_sdss_id",
                  view_kind="table", view_comment="Last refreshed: 2026-11-18",
                  view_last_refreshed="2026-11-18", reason="", staleness_warnings=[])
    lookup_attrs = apogee.apogee_id_source_attrs(source)

    path = os.path.join(tmp, "synthetic.h5")
    with h5.File(path, "w") as fp:
        for exposures in (fps, plate):
            obs, mjd = exposures[0].observatory, exposures[0].mjd
            sequences = {
                "objects": apogee.get_science_sequences(exposures),
                "arclamps": apogee.get_arclamp_sequences(exposures),
                "missing": apogee.get_sequences(exposures, "missing", ()),
            }
            io.update(fp, obs, mjd, exposures, sequences, fibers=True, compression=False)
            io.write_night_attrs(fp, obs, mjd, overrides_attrs(True), lookup_attrs)
        rows = [dict(observatory="apo", mjd=60000, exposure=e, expected_max_db=5, reason=r)
                for e, r in ((4, "db_no_file"), (6, "trailing"))]
        io.write_missing_exposures(fp, rows)

    def fake(ids, suffix=""):
        return {i: dict(sdss_id=i, catalogid=10_000 + i, version_id=31, lead="gaia_dr3_source",
                        carton_pks={1}) for i in ids}

    with contextlib.redirect_stdout(open(os.devnull, "w")):
        default_attrs = metadata.build_metadata(path, query_batch=fake, query_workers=0)
        ext_path = os.path.join(tmp, "targets_extended.h5")
        ext_attrs = metadata.build_metadata(path, output_path=ext_path, query_batch=fake,
                                            query_workers=0, extended=True,
                                            checkpoint_dir=os.path.join(tmp, "ck_ext"))
    with h5.File(path, "r") as fp:
        solar_row = {k: (fp["meta"][k][list(fp["meta/sdss_id"][:]).index(0)].item()
                         if fp["meta"][k].ndim == 1 else "array")
                     for k in ("sdss_id", "catalogid", "lead")}
        seqs = {k: fp[f"raw/apo/60000/sequences/{k}"][:].tolist() for k in fp["raw/apo/60000/sequences"]}
    return dict(
        file=h5_listing(path), extended_file=h5_listing(ext_path),
        meta_attrs=sorted(default_attrs), meta_attrs_extended=sorted(ext_attrs),
        solar_row={k: (v.decode() if isinstance(v, bytes) else v) for k, v in solar_row.items()},
        fps_sequences=seqs, night_attrs=sorted(set(overrides_attrs(True)) | set(lookup_attrs)),
        synthetic_table_id=synthetic,
    )


# --------------------------------------------------------------------------- #
# Doc parsing
# --------------------------------------------------------------------------- #


def doc_files(repo):
    files = []
    for g in DOC_GLOBS:
        files.extend(sorted(Path(repo).glob(g)))
    return files


def code_blocks(text, path):
    """[(line_no, line)] of lines inside fenced (md) or code-block (rst) blocks."""
    lines = text.splitlines()
    out = []
    if path.suffix == ".md":
        inside = False
        for i, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                out.append((i, line))
    else:
        inside, indent = False, None
        for i, line in enumerate(lines, 1):
            if line.strip().startswith(".. code-block::"):
                inside, indent = True, None
                continue
            if inside:
                if not line.strip():
                    continue
                cur = len(line) - len(line.lstrip())
                if indent is None:
                    indent = cur
                if cur < indent or cur == 0:
                    inside = False
                    continue
                out.append((i, line))
    return out


def join_continuations(numbered):
    out, buf, start = [], "", None
    for i, line in numbered:
        s = line.rstrip()
        cont = s.endswith("\\")
        s = s.rstrip("\\").rstrip()
        if start is None:
            start = i
        buf = (buf + " " + s.strip()) if buf else s.strip()
        if not cont:
            out.append((start, buf))
            buf, start = "", None
    if buf:
        out.append((start, buf))
    return out


def check_command(tree, tokens, where):
    """Resolve `almanac ...` tokens; report unknown commands / options."""
    path = ["almanac"]
    i = 1
    while i < len(tokens):
        entry = tree[" ".join(path)]
        t = tokens[i]
        if t.startswith("-"):
            name = t.split("=", 1)[0]
            opts = option_set(entry)
            if (re.fullmatch(r"-v+", name) and "-v" in opts) or name == "--help":
                pass
            elif name not in opts:
                # an option of a parent command used after the subcommand?
                parents = [" ".join(path[:k]) for k in range(1, len(path))]
                owner = [p for p in parents if name in option_set(tree[p])]
                if owner:
                    problem("ERROR", where, f"`{name}` is an option of `{owner[-1]}`, not of `{' '.join(path)}`")
                else:
                    problem("ERROR", where, f"unknown option `{name}` for `{' '.join(path)}`")
            else:
                # skip the option's value
                p = next(p for p in entry["params"] if p["kind"] == "option" and name in p["opts"])
                if not (p["is_flag"] or p["count"]) and "=" not in t:
                    i += 1
        elif entry["group"] and t in entry["subcommands"]:
            path.append(t)
        elif entry["group"] and path == ["almanac"] and not t.startswith("<") and re.fullmatch(r"[a-z]+", t):
            problem("ERROR", where, f"unknown subcommand `almanac {t}`")
        i += 1
    return " ".join(path)


def check_commands_in_docs(repo, tree):
    used = {}
    for path in doc_files(repo):
        text = path.read_text()
        rel = path.relative_to(repo)
        for line_no, line in join_continuations(code_blocks(text, path)):
            s = line.strip()
            if s.startswith("$ "):
                s = s[2:]
            m = re.match(r"(?:\S+=\S+\s+)*(?:uv run (?:--no-sync )?)?(almanac(?:\s.*)?)$", s)
            if not m:
                continue
            cmdline = m.group(1)
            if "|" in cmdline:
                cmdline = cmdline.split("|")[0]
            try:
                tokens = shlex.split(cmdline, comments=True)
            except ValueError:
                tokens = cmdline.split()
            where = f"{rel}:{line_no}"
            cmd = check_command(tree, tokens, where)
            used.setdefault(cmd, []).append(where)
            if "--no-x-match" in tokens and "not recommended" not in line:
                problem("ERROR", where, "`--no-x-match` in an example not marked `# not recommended`")
        for line_no, line in enumerate(text.splitlines(), 1):
            for span in re.findall(r"`{1,2}(almanac [^`]+)`{1,2}", line):
                tokens = span.split()
                check_command(tree, tokens, f"{rel}:{line_no} (inline)")
    return used


def check_inline_options(repo, tree, allow=("--p",)):
    all_opts = set()
    for entry in tree.values():
        all_opts |= option_set(entry)
    for path in doc_files(repo):
        rel = path.relative_to(repo)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            for opt in re.findall(r"`{1,2}(--[a-z][a-z0-9-]*)", line):
                if opt not in all_opts and opt not in allow:
                    problem("ERROR", f"{rel}:{line_no}", f"inline option `{opt}` is not an option of any command")
            for opt in re.findall(r"`{1,2}(-[a-zA-Z])\b", line):
                if opt not in all_opts:
                    problem("ERROR", f"{rel}:{line_no}", f"inline option `{opt}` is not an option of any command")


def check_config(repo, keys):
    for path in doc_files(repo):
        rel = path.relative_to(repo)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            for key in re.findall(r"almanac config (?:get|set) ([A-Za-z_][A-Za-z0-9_.]*)", line):
                if key not in keys and not any(k.startswith(key + ".") for k in keys):
                    problem("ERROR", f"{rel}:{line_no}", f"unknown configuration key `{key}`")
    conf = Path(repo) / "docs/configuration.md"
    if conf.exists():
        documented = set(re.findall(r"^#### `([A-Za-z_][A-Za-z0-9_.]*)`", conf.read_text(), re.M))
        for k in sorted(set(keys) - documented):
            problem("ERROR", "docs/configuration.md", f"configuration key `{k}` is not documented")
        for k in sorted(documented - set(keys)):
            problem("ERROR", "docs/configuration.md", f"documented configuration key `{k}` does not exist")


def section_tables(text, heading_regex):
    """Rows `| \`name\` | ... |` under a heading matching heading_regex, until the next ## heading."""
    rows = []
    inside = False
    for line in text.splitlines():
        if re.match(r"^## ", line):
            inside = bool(re.match(heading_regex, line))
            continue
        if inside and line.startswith("| `"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows


def check_model_tables(repo, mdl):
    path = Path(repo) / "docs/data-formats.md"
    text = path.read_text()
    for model, heading in (("Exposure", r"^## Exposure Model"), ("FPSTarget", r"^## FPSTarget Model"),
                           ("PlateTarget", r"^## PlateTarget Model")):
        rows = section_tables(text, heading)
        names = {}
        for cells in rows:
            m = re.match(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0])
            if not m:
                continue
            alias = None
            for c in cells[1:3]:
                a = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", c)
                if a and not re.fullmatch(r"(int|float|str|bool|Int64|Observatory|Prefix|ImageType|Category|HoleType|ObjType|Optional\[.*\])", a.group(1)):
                    alias = a.group(1)
            names[m.group(1)] = alias
        truth = set(mdl[model]["fields"]) | set(mdl[model]["computed"])
        for n in sorted(truth - set(names)):
            problem("ERROR", f"docs/data-formats.md ({model})", f"field `{n}` is not documented")
        for n in sorted(set(names) - truth):
            problem("ERROR", f"docs/data-formats.md ({model})", f"documented field `{n}` does not exist")
        for n, alias in names.items():
            if n in mdl[model]["fields"]:
                real = mdl[model]["fields"][n]["alias"]
                if alias != real:
                    problem("ERROR", f"docs/data-formats.md ({model})",
                            f"field `{n}`: documented alias {alias!r}, code alias {real!r}")


def marked_tables(text):
    """{NAME: [first-column names]} for tables after `<!-- audit:table NAME -->`."""
    out, current = {}, None
    for line in text.splitlines():
        m = re.match(r"<!-- audit:table ([a-z_]+) -->", line.strip())
        if m:
            current = m.group(1)
            out[current] = []
            continue
        if current:
            if line.startswith("| `"):
                n = re.match(r"\| `([^`]+)`", line)
                out[current].append(n.group(1))
            elif line.startswith("|"):
                continue
            elif line.strip() == "" and out[current]:
                current = None
    return out


def check_hdf5_tables(repo, synth):
    path = Path(repo) / "docs/data-formats.md"
    tables = marked_tables(path.read_text())
    f = synth["file"]
    truth = {
        "night_attrs": set(f["/raw/apo/60000"]["attrs"]),
        "missing_exposures": {k.split("/")[-1] for k in f if k.startswith("/missing_exposures/")},
        "sequences": {k.split("/")[-1] for k in f if k.startswith("/raw/apo/60000/sequences/")},
        "meta_attrs": set(synth["meta_attrs"]) | set(synth["meta_attrs_extended"]),
        "meta_side_groups": {k[1:] for k in f if k.startswith("/meta_") and f[k]["kind"] == "dataset"},
    }
    for name, expected in truth.items():
        if name not in tables:
            problem("ERROR", "docs/data-formats.md", f"no `<!-- audit:table {name} -->` table")
            continue
        documented = set(tables[name])
        for n in sorted(expected - documented):
            problem("ERROR", f"docs/data-formats.md ({name})", f"`{n}` is written but not documented")
        for n in sorted(documented - expected):
            problem("ERROR", f"docs/data-formats.md ({name})", f"`{n}` is documented but not written")


def lint(repo, version):
    for path in doc_files(repo):
        rel = path.relative_to(repo)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            where = f"{rel}:{line_no}"
            if "users.flatironinstitute.org" in line:
                problem("ERROR", where, "public-web URL")
            if re.search(r"(?<![\w.])/mnt/", line) or (
                # The Utah defaults of the path settings are code defaults, listed
                # as such in the configuration reference only.
                re.search(r"(?<![\w.])/uufs/", line) and str(rel) != "docs/configuration.md"
            ):
                problem("ERROR", where, "cluster-internal path (use data/work/mwm/... form)")
            for w in re.findall(r"\b[Ff]ibres?\b", line.replace("--fibres", "")):
                problem("ERROR", where, f"spelling `{w}` (US: fiber)")
            for v in re.findall(r"\b(?:version|almanac)\s+v?(\d+\.\d+\.\d+)\b", line):
                if v != version and not re.search(r"upstream|sdss/almanac|0\.4\.\d", line):
                    problem("WARN", where, f"version {v} mentioned (package is {version})")


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def write_ground_truth_md(out, tree, mdl, keys, synth, version):
    L = [f"# almanac ground truth (version {version})", ""]
    L += ["## Commands", ""]
    for path, e in tree.items():
        L += [f"### `{path}`" + (" (hidden)" if e["hidden"] else ""), "", "```", e["help"].rstrip(), "```", ""]
    L += ["## Configuration keys", "", "| key | default |", "|---|---|"]
    L += [f"| `{k}` | `{v}` |" for k, v in keys.items()]
    L += ["", "## Models", ""]
    for name in ("Exposure", "FPSTarget", "PlateTarget"):
        L += [f"### {name}", "", "| field | alias |", "|---|---|"]
        L += [f"| `{n}` | {('`' + f['alias'] + '`') if f['alias'] else ''} |" for n, f in mdl[name]["fields"].items()]
        L += [f"| `{n}` | (computed) |" for n in mdl[name]["computed"]]
        L += [""]
    L += ["## Synthetic almanac file", "", "| path | kind | dtype | shape | attrs |", "|---|---|---|---|---|"]
    for p, e in synth["file"].items():
        L.append(f"| `{p}` | {e['kind']} | {e.get('dtype', '')} | {e.get('shape', '')} | {', '.join(e['attrs'])} |")
    L += ["", "### Extra in the `--extended` targets file (/meta datasets)", ""]
    base = {p for p in synth["file"] if p.startswith("/meta/")}
    L += [f"- `{p}`" for p in synth["extended_file"] if p.startswith("/meta/") and p not in base]
    L += ["", f"/meta attributes (default): {', '.join(synth['meta_attrs'])}", "",
          f"/meta attributes (--extended): {', '.join(synth['meta_attrs_extended'])}", "",
          f"solar-system /meta row: {synth['solar_row']}", "",
          f"FPS night sequences: {synth['fps_sequences']}", ""]
    (out / "ground_truth.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    out = Path(args.out or tempfile.mkdtemp(prefix="almanac_docs_audit_")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo / "src"))

    import almanac
    version = almanac.__version__
    tree = cli_tree()
    mdl = models()
    keys = config_keys()
    with tempfile.TemporaryDirectory() as tmp:
        synth = build_synthetic(tmp)

    (out / "ground_truth.json").write_text(json.dumps(
        dict(version=version, cli=tree, models=mdl, config=keys, synthetic=synth), indent=1, default=str))
    write_ground_truth_md(out, tree, mdl, keys, synth, version)

    check_commands_in_docs(repo, tree)
    check_inline_options(repo, tree)
    check_config(repo, keys)
    check_model_tables(repo, mdl)
    check_hdf5_tables(repo, synth)
    lint(repo, version)

    errors = [p for p in problems if p[0] == "ERROR"]
    L = [f"# almanac documentation audit ({repo.name}, version {version})", "",
         f"{len(errors)} error(s), {len(problems) - len(errors)} warning(s)", ""]
    L += [f"- **{lvl}** {where}: {msg}" for lvl, where, msg in problems]
    (out / "audit_report.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nground truth and report in {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
