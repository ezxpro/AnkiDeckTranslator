import os
import sys
from pathlib import Path


def _project_root() -> Path:
    """Folder holding .env, config.json and the caches."""
    if getattr(sys, "frozen", False):                      # standalone binary / AppImage
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        folder = Path(base) / "ankitool"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return Path.cwd()
    return Path.cwd()                                      # pip install, no checkout


SCRIPT_DIR = _project_root()