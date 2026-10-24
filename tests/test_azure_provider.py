import contextlib
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from ankitool.core.errors import ConfigError
from ankitool.core.settings import env_value
from ankitool.providers.azure import AzureProvider
from ankitool.providers.base import PROVIDERS


def _reply(body, lang="it"):
    return [{"detectedLanguage": {"language": lang, "score": 1.0},
             "translations": [{"text": f"PT:{b['Text']}", "to": "pt"}]} for b in body]


LANGS = {"translation": {"it": {"name": "Italian"}, "pt": {"name": "Portuguese (Brazil)"},
                         "pt-pt": {"name": "Portuguese (Portugal)"}, "en": {"name": "English"},
                         "zh-Hans": {"name": "Chinese Simplified"}, "tlh-Latn": {"name": "Klingon (Latin)"}}}


def _provider(monkeypatch, script, langs=LANGS):
    """Real provider; only urllib's urlopen is replaced. script(body, call_number) -> reply or raises.
    The language-list request (a GET) is answered with `langs`; None makes it fail."""
    monkeypatch.setenv("AZURE_REGION", "brazilsouth")
    calls = []

    def urlopen(req, timeout=None):
        if req.data is None:
            if langs is None:
                raise urllib.error.URLError("offline")
            return io.BytesIO(json.dumps(langs).encode())
        body = json.loads(req.data)
        calls.append((req.full_url, {k.lower(): v for k, v in req.header_items()}, body))
        return io.BytesIO(json.dumps(script(body, len(calls))).encode())

    patcher = patch("ankitool.providers.azure.urllib.request.urlopen", urlopen)
    patcher.start()
    return AzureProvider(key="test"), calls, patcher


def test_registered_and_languages(monkeypatch):
    p, _, patcher = _provider(monkeypatch, lambda b, n: _reply(b))
    try:
        assert "azure" in PROVIDERS
        assert p.resolve_target("pt-br") == "PT-BR"
        assert p.source_names["IT"] == "Italian"
    finally:
        patcher.stop()


def test_languages_come_from_azure(monkeypatch):
    p, calls, patcher = _provider(monkeypatch, lambda b, n: _reply(b))
    try:
        assert p.target_codes == {"IT", "PT-BR", "PT-PT", "EN-GB", "EN-US", "ZH-HANS", "TLH-LATN"}
        assert p.source_names["PT"] == "Portuguese" and p.source_names["TLH"] == "Klingon"
        p.translate(["x"], "TLH-LATN")
        p.translate(["x"], "ZH-HANS", "ZH")
    finally:
        patcher.stop()
    assert "to=tlh-Latn" in calls[0][0]                  # Azure's own spelling is used on the wire
    assert "to=zh-Hans" in calls[1][0] and "from=zh-Hans" in calls[1][0]


def test_built_in_table_is_used_when_azure_list_fails(monkeypatch):
    p, _, patcher = _provider(monkeypatch, lambda b, n: _reply(b), langs=None)
    try:
        assert p.resolve_target("eu") == "EU" and p.resolve_target("pt-br") == "PT-BR"
    finally:
        patcher.stop()


def test_request_shape_and_result(monkeypatch):
    p, calls, patcher = _provider(monkeypatch, lambda b, n: _reply(b))
    try:
        res = p.translate(["ciao"], "PT-BR", "IT")
    finally:
        patcher.stop()
    url, headers, body = calls[0]
    assert "to=pt&" in url + "&" and "from=it" in url and "textType" not in url
    assert headers["ocp-apim-subscription-key"] == "test"
    assert headers["ocp-apim-subscription-region"] == "brazilsouth"
    assert body == [{"Text": "ciao"}]
    assert res[0].text == "PT:ciao" and res[0].detected_source_lang == "IT"
    assert p.usage() == "4 characters sent in this run"


def test_tags_are_protected_and_restored(monkeypatch):
    p, calls, patcher = _provider(monkeypatch, lambda b, n: _reply(b))
    try:
        res = p.translate(["ciao [sound:a.mp3] {{c1::casa}}"], "PT-BR", "IT", protect_tags=True)
    finally:
        patcher.stop()
    assert "textType=html" in calls[0][0]
    assert '<span class="notranslate">[sound:a.mp3]</span>' in calls[0][2][0]["Text"]
    assert res[0].text == "PT:ciao [sound:a.mp3] {{c1::casa}}"


def test_big_batches_are_split(monkeypatch):
    p, calls, patcher = _provider(monkeypatch, lambda b, n: _reply(b))
    try:
        res = p.translate(["a" * 30_000, "b" * 30_000], "PT-BR")
    finally:
        patcher.stop()
    assert len(calls) == 2 and len(res) == 2


def test_rate_limit_is_retried(monkeypatch):
    def script(body, n):
        if n == 1:
            raise urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "1"}, None)
        return _reply(body)
    p, calls, patcher = _provider(monkeypatch, script)
    try:
        with patch("ankitool.providers.base.time.sleep"):
            res = p.translate(["x"], "PT-BR")
    finally:
        patcher.stop()
    assert res[0].text == "PT:x" and len(calls) == 2


def test_bad_key_raises_config_error(monkeypatch):
    def script(body, n):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    p, _, patcher = _provider(monkeypatch, script)
    try:
        with pytest.raises(ConfigError):
            p.translate(["x"], "PT-BR")
    finally:
        patcher.stop()


def test_missing_region_warns_early():
    err = io.StringIO()

    def offline(req, timeout=None):
        raise urllib.error.URLError("offline")

    with patch("ankitool.providers.azure.setting", lambda name, default="": ""), \
            patch("ankitool.providers.azure.urllib.request.urlopen", offline), \
            contextlib.redirect_stderr(err):
        AzureProvider(key="test")
    assert "AZURE_REGION is not set" in err.getvalue()


@pytest.mark.skipif(not env_value("AZURE_KEY"), reason="no AZURE_KEY: live test skipped")
def test_live_azure_smoke():
    p = AzureProvider()
    texts = ["Il gatto mangia [sound:a.mp3] {{c1::il pesce}}", "3155"]
    res = p.translate(texts, "PT-BR", None, protect_tags=True)      # no source given: Azure detects it
    assert "[sound:a.mp3]" in res[0].text and "{{c1::" in res[0].text and "notranslate" not in res[0].text
    assert res[0].detected_source_lang == "IT"
    assert p.translate(texts[:1], "PT-BR", "IT")[0].text            # with a source, Azure reports no detection