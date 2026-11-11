from pathlib import Path
from types import SimpleNamespace

from ankitool.core.env_template import ENV_TEMPLATE


def test_template_matches_env_example():
    root = Path(__file__).resolve().parents[1]
    assert (root / ".env.example").read_text(encoding="utf-8") == ENV_TEMPLATE


def test_init_creates_env_and_never_overwrites_without_force(tmp_path, monkeypatch):
    import ankitool.cli.initcmd as initcmd
    monkeypatch.setattr(initcmd, "SCRIPT_DIR", tmp_path)
    env = tmp_path / ".env"

    initcmd.run_init(SimpleNamespace(force=False))
    assert env.read_text(encoding="utf-8") == ENV_TEMPLATE

    env.write_text("KEEP", encoding="utf-8")
    initcmd.run_init(SimpleNamespace(force=False))
    assert env.read_text(encoding="utf-8") == "KEEP"

    initcmd.run_init(SimpleNamespace(force=True))
    assert env.read_text(encoding="utf-8") == ENV_TEMPLATE
