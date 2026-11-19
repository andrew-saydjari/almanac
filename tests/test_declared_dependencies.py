"""Every third-party module almanac imports must be a declared dependency.

A package that is only present as a transitive dependency (or installed by
hand in one environment) works until the dependency that pulled it in changes;
an undeclared new import (e.g. ``healpy``) is not installed by
``uv sync`` / ``uv pip install -e`` at all. This test parses the imports of
``src/almanac`` and checks each against ``[project.dependencies]`` in
``pyproject.toml``. It needs no database and no network.
"""

import ast
import importlib.metadata as im
import pathlib
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "almanac"


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_distributions() -> set:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    names = set()
    for requirement in project.get("dependencies", []):
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        names.add(_normalise(match.group(1)))
    return names


def imported_top_level_modules() -> dict:
    """{top-level module: [files]} for absolute imports in src/almanac."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def third_party_imports() -> dict:
    return {
        module: files
        for module, files in imported_top_level_modules().items()
        if module not in sys.stdlib_module_names and module != "almanac"
    }


def test_every_import_is_a_declared_dependency():
    declared = declared_distributions()
    module_to_dists = im.packages_distributions()
    problems = []
    for module, files in sorted(third_party_imports().items()):
        dists = {_normalise(d) for d in module_to_dists.get(module, [])}
        if not dists:
            problems.append(f"{module}: not installed, and so not declared (imported in {sorted(files)})")
        elif not dists & declared:
            problems.append(
                f"{module}: provided by {sorted(dists)}, none of which is in "
                f"[project.dependencies] (imported in {sorted(files)})"
            )
    assert not problems, "Undeclared imports:\n  " + "\n  ".join(problems)


def test_scan_sees_known_imports():
    # Guard against the scan silently finding nothing.
    modules = third_party_imports()
    for module in ("numpy", "h5py", "click", "sdssdb", "healpy"):
        assert module in modules, module


def test_declared_dependencies_are_locked():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {_normalise(p["name"]) for p in lock.get("package", [])}
    missing = sorted(declared_distributions() - locked)
    assert not missing, f"declared but not in uv.lock (run `uv lock`): {missing}"
