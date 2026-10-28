import json
from unittest.mock import patch

from ankitool.providers.base import PROVIDERS, ConfigError
from ankitool.providers.gemini import GeminiProvider, _Retryable


class FakeGemini(GeminiProvider):
    """Real parsing/batching/retry logic; only the SDK call is replaced by `script`.
    script(system, items) -> raw reply string (or raises). Every call is recorded."""
    rpm = None                                 # no pacing in tests
    
    def __init__(self, script):
        self.script, self.calls = script, []
        super().__init__(key="test")

    def connect(self, key):
        pass

    def _generate(self, system, payload):
        items = json.loads(payload)
        self.calls.append((system, items))
        return self.script(system, items)


def _ok(items, lang="IT"):
    return json.dumps([{"id": i["id"], "lang": lang, "text": f"PT:{i['text']}"} for i in items],
                      ensure_ascii=False)


def test_registered_and_languages():
    assert "gemini" in PROVIDERS
    p = FakeGemini(lambda s, i: _ok(i))
    assert p.resolve_target("pt-br") == "PT-BR"
    assert p.source_names["PT"] == "Portuguese"          # base code, region stripped
    assert p.usage() is None


def test_translate_protects_tags_and_reports_language():
    p = FakeGemini(lambda s, i: _ok(i))
    res = p.translate(["ciao [sound:a.mp3] {{c1::casa}}"], "PT-BR", "IT", protect_tags=True)
    sent = p.calls[0][1][0]["text"]
    assert "⟦0⟧" in sent and "[sound:" not in sent                  # masked before the call
    assert res[0].text == "PT:ciao [sound:a.mp3] {{c1::casa}}"      # unmasked afterwards
    assert res[0].detected_source_lang == "IT"
    assert "Portuguese (Brazilian)" in p.calls[0][0] and "Italian" in p.calls[0][0]


def test_reordered_ids_are_matched_by_id():
    def script(s, items):
        return _ok(list(reversed(items)))
    res = FakeGemini(script).translate(["a", "b", "c"], "PT-BR")
    assert [r.text for r in res] == ["PT:a", "PT:b", "PT:c"]


def test_json_fence_is_accepted():
    p = FakeGemini(lambda s, i: "```json\n" + _ok(i) + "\n```")
    assert p.translate(["x"], "PT-BR")[0].text == "PT:x"


def test_wrong_item_count_splits_the_batch():
    def script(s, items):
        if len(items) > 1:                                # drops one item whenever batch > 1
            return _ok(items[:-1])
        return _ok(items)
    p = FakeGemini(script)
    res = p.translate(["a", "b", "c", "d"], "PT-BR")
    assert [r.text for r in res] == ["PT:a", "PT:b", "PT:c", "PT:d"]
    assert len(p.calls) > 1


def test_garbage_reply_gives_none_after_retries():
    p = FakeGemini(lambda s, i: "sorry, I cannot help")
    with patch("ankitool.providers.base.time.sleep"):
        assert p.translate(["a"], "PT-BR") is None
    assert len(p.calls) == p.retries


def test_rate_limit_is_retried_then_succeeds():
    state = {"n": 0}

    def script(s, items):
        state["n"] += 1
        if state["n"] == 1:
            raise _Retryable("429")
        return _ok(items)
    p = FakeGemini(script)
    with patch("ankitool.providers.base.time.sleep"):
        assert p.translate(["a"], "PT-BR")[0].text == "PT:a"


def test_detect_uses_a_cheap_call_and_returns_base_codes():
    def script(s, items):
        return json.dumps([{"id": i["id"], "lang": "pt-BR"} for i in items])
    p = FakeGemini(script)
    assert p.detect(["olá", "tudo bem"], "IT") == ["PT", "PT"]
    assert "Identify the language" in p.calls[0][0]       # detection prompt, not translation


def test_detect_returns_none_when_unavailable():
    def script(s, items):
        raise _Retryable("down")
    p = FakeGemini(script)
    with patch("ankitool.providers.base.time.sleep"), patch("ankitool.providers.gemini.time.sleep"):
        assert p.detect(["a"], "PT-BR") is None


def test_missing_sdk_gives_clear_error():
    import builtins
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("google"):
            raise ImportError(name)
        return real(name, *a, **k)
    with patch("builtins.__import__", fake_import):
        try:
            GeminiProvider(key="x")
        except ConfigError as e:
            assert "google-genai" in str(e)
        else:
            raise AssertionError("expected ConfigError")

def test_wait_is_reported_before_retry():
    state, seen = {"n": 0}, []

    def script(s, items):
        state["n"] += 1
        if state["n"] == 1:
            raise _Retryable("429", 20)
        return _ok(items)
    p = FakeGemini(script)
    p.on_wait = lambda seconds, why: seen.append((seconds, why))
    with patch("ankitool.providers.base.time.sleep") as sl:
        assert p.translate(["a"], "PT-BR")[0].text == "PT:a"
    assert seen == [(20, "429")] and sl.call_args_list[0][0][0] == 20
