import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ankitool.cli.config import ConfigApp
from ankitool.core.errors import ConfigError
from ankitool.core.settings import Settings, configured_provider
from ankitool.providers.openai_compat import GroqProvider


def _tmp():
    return Path(tempfile.mkdtemp()) / "config.json"


def test_roundtrip_and_missing_file():
    p = _tmp()
    st = Settings(p)
    assert st.data == {} and st.error is None
    st.set("groq", "provider"); st.set(5, "providers", "groq", "max_workers"); st.save()
    again = Settings(p)
    assert again.get("provider") == "groq" and again.get("providers", "groq", "max_workers") == 5
    assert again.get("providers", "x", "model", default="d") == "d"
    again.unset("providers", "groq", "max_workers")
    assert again.get("providers", "groq", "max_workers") is None


def test_invalid_file_is_ignored_with_error():
    p = _tmp(); p.write_text("{not json", encoding="utf-8")
    st = Settings(p)
    assert st.data == {} and "invalid" in st.error


def test_env_beats_config_for_provider():
    st = Settings(_tmp()); st.set("groq", "provider")
    with patch("ankitool.core.settings._instance", st):
        with patch.dict("os.environ", {"ANKITOOL_PROVIDER": ""}):
            assert configured_provider() == "groq"
        with patch.dict("os.environ", {"ANKITOOL_PROVIDER": "deepl"}):
            assert configured_provider() == "deepl"


def test_config_command_set_and_validate():
    st = Settings(_tmp())
    run = lambda action, key=None, value=None: ConfigApp(
        SimpleNamespace(action=action, key=key, value=value)).run()
    with patch("ankitool.cli.config.get_settings", lambda: st):
        run("set", "provider", "groq"); run("set", "groq.max_workers", "4"); run("set", "groq.model", "m2")
        for bad in (("set", "provider", "nope"), ("set", "groq.max_workers", "0"),
                    ("set", "groq.color", "x"), ("set", "provider", None), ("set", None, None)):
            with pytest.raises(ConfigError):
                run(*bad)
    saved = Settings(st.path)
    assert saved.get("provider") == "groq"
    assert saved.get("providers", "groq") == {"max_workers": 4, "model": "m2"}


def test_env_value_reads_env_then_dotenv():
    from ankitool.core.settings import env_value
    f = _tmp().with_name(".env")
    f.write_text('# c\nexport GROQ_MODEL="m-file"\nANKITOOL_PROVIDER=groq\n', encoding="utf-8")
    assert env_value("GROQ_MODEL", f) == "m-file" and env_value("NOPE_X", f) == ""
    with patch.dict("os.environ", {"GROQ_MODEL": "m-env"}):
        assert env_value("GROQ_MODEL", f) == "m-env"


def test_provider_uses_config_but_env_wins():
    st = Settings(_tmp())
    st.set("cfg-model", "providers", "groq", "model"); st.set(7, "providers", "groq", "max_workers")
    st.set("https://cfg.example.com/v1", "providers", "groq", "base_url")
    with patch("ankitool.core.settings._instance", st):
        # Isolate from the developer's .env file (which may set GROQ_MODEL/GROQ_BASE_URL),
        # so config.json is the only source unless the environment is set explicitly.
        with patch("ankitool.providers.openai_compat.setting",
                   side_effect=lambda name, default="": os.environ.get(name, default)):
            p = GroqProvider(key="k")
            assert (p.model, p.base_url, p.max_workers) == ("cfg-model", "https://cfg.example.com/v1", 7)   
            with patch.dict("os.environ", {"GROQ_MODEL": "env-model"}):
                assert GroqProvider(key="k").model == "env-model"
