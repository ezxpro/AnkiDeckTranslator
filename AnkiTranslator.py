"""Compatibility launcher: `python AnkiTranslator.py ...` keeps working from a checkout.
Prefer `pip install -e .` and the `ankitool` command, or `python -m ankitool`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ankitool.cli.main import main  # noqa: E402

if __name__ == "__main__":
    main()
