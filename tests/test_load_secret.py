
import tempfile
from pathlib import Path
from unittest.mock import patch
import os
import pytest

from ankitool.core.errors import ConfigError
from ankitool.providers.base import load_secret


def _env_dir(contents):
    d = Path(tempfile.mkdtemp())
    (d / ".env").write_text(contents, encoding="utf-8")
    return d


def _load(env_name, contents):
    with patch.dict(os.environ, {}), \
         patch("ankitool.core.settings.SCRIPT_DIR", _env_dir(contents)):
        os.environ.pop(env_name, None)
        return load_secret(env_name, "Test")


def test_inline_comment_is_ignored():
    assert _load("GROQ_KEY", "GROQ_KEY=gsk_abc   # free tier, from console\n") == "gsk_abc"


def test_quoted_value_still_strips_quotes():
    assert _load("GROQ_KEY", 'GROQ_KEY="gsk_abc" # comment\n') == "gsk_abc"


def test_missing_key_raises_config_error():
    with pytest.raises(ConfigError):
        _load("NOPE_KEY", "OTHER=value\n")
