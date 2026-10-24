"""Catches names used but never imported/defined (e.g. a helper moved to another module).

Such a mistake only shows up at run time, on the code path that uses the name, and the CLI
reports it as a generic "[Unexpected error] name 'x' is not defined". This test parses every
module under src/ and fails on any loaded name that is not bound anywhere in the same file.
"""
import ast
import builtins
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
KNOWN = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def _bound_names(tree):
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            names.update(x.arg for x in a.posonlyargs + a.args + a.kwonlyargs)
            names.update(x.arg for x in (a.vararg, a.kwarg) if x)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((al.asname or al.name).split(".")[0] for al in n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def test_no_undefined_names():
    problems = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        known = _bound_names(tree) | KNOWN
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known:
                problems.append(f"{path.relative_to(SRC)}:{n.lineno}: '{n.id}'")
    assert not problems, "undefined names:\n" + "\n".join(problems)
