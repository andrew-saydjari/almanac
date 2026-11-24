"""Every name a CLI command reads is defined (bug E: `postprocess` raised a
NameError on `unknown_carton_pks` after writing its files)."""

import ast
import builtins
from pathlib import Path

import almanac.cli

SOURCE = Path(almanac.cli.__file__).read_text()


def _bound_names(node):
    bound = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            bound.add(sub.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(sub.name)
            if not isinstance(sub, ast.ClassDef):
                a = sub.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                    if arg is not None:
                        bound.add(arg.arg)
        elif isinstance(sub, ast.Lambda):
            for arg in sub.args.args:
                bound.add(arg.arg)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            bound.add(sub.name)
    return bound


def undefined_names(function_name):
    tree = ast.parse(SOURCE)
    module_names = _bound_names(ast.Module(body=[
        n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ], type_ignores=[])) | {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    defined = _bound_names(function) | module_names | set(dir(builtins))
    return sorted({
        n.id for n in ast.walk(function)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined
    })


def test_postprocess_names_defined():
    assert undefined_names("postprocess") == []


def test_command_names_defined():
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            assert undefined_names(node.name) == [], node.name
