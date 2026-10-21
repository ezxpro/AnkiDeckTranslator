from pathlib import Path

# Project root (the folder holding .env and the caches). Temporary: step 4 of the
# refactor replaces this with per-user dirs (platformdirs).
SCRIPT_DIR = Path(__file__).resolve().parents[3]
