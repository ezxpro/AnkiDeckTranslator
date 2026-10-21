from pathlib import Path


def _project_root() -> Path:
    """Folder holding .env and the caches.

    When run from a checkout, this is the repository root (found by walking up to
    the nearest ``.git`` directory). When installed as a package (``pip install``),
    there is no checkout; fall back to the package's top-level source directory so
    the program still has a deterministic place to look for ``.env`` and its caches.

    Temporary: a later refactor replaces this with per-user dirs (platformdirs).
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    # Installed (no checkout): use the directory four levels up from core/paths.py,
    # i.e. the project root that holds ``src/``.
    return here.parents[3]


SCRIPT_DIR = _project_root()
