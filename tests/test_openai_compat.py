import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from ankitool.core.errors import ConfigError
from ankitool.providers.base import PROVIDERS
from ankitool.providers.openai_compat import GroqProvider


def _resp(items):
    text = json.dumps([{"id": i["id"], "lang": "EN", "text": "PT:" + i["text"]} for i in items])
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def test_registered_and_request_shape():
    assert {"openai", "groq"} <= set(PROVIDERS)
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        return _resp(json.loads(seen["body"]["messages"][1]["content"]))
    with patch.dict("os.environ", {"GROQ_BASE_URL": "http://localhost:1/v1/", "GROQ_MODEL": "m1"}), \
            patch("urllib.request.urlopen", fake_urlopen):
        p = GroqProvider(key="k")
        res = p.translate(["hello"], "PT-BR", "EN")
    assert res[0].text == "PT:hello" and res[0].detected_source_lang == "EN"
    assert seen["url"] == "http://localhost:1/v1/chat/completions"
    assert seen["auth"] == "Bearer k" and seen["body"]["model"] == "m1"


def test_429_uses_retry_after_and_reports_wait():
    calls, waits = [], []

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "x", {"Retry-After": "7"}, None)
        return _resp(json.loads(json.loads(req.data)["messages"][1]["content"]))
    p = GroqProvider(key="k")
    p.on_wait = lambda s, why: waits.append(s)
    with patch("urllib.request.urlopen", fake_urlopen), patch("ankitool.providers.base.time.sleep"):
        assert p.translate(["a"], "PT-BR")[0].text == "PT:a"
    assert waits == [7]


def test_bad_key_is_a_config_error_not_retried():
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b"bad key"))
    p = GroqProvider(key="k")
    with patch("urllib.request.urlopen", fake_urlopen), pytest.raises(ConfigError, match="bad key"):
        p.translate(["a"], "PT-BR")
