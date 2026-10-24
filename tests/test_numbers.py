from ankitool.cache.translation import CacheView, TranslationCache
from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools
from ankitool.ops.translate import FieldTranslator
from ankitool.providers.base import TranslationResult


class Recorder:
    name = "rec"
    label = "rec"
    batch_size = 40
    max_workers = 1

    def __init__(self):
        self.sent = []

    def mask(self, t):
        return TextTools.mask(t)

    def translate(self, texts, target_lang, source_lang=None, protect_tags=False):
        self.sent += texts
        return [TranslationResult(t) for t in texts]


def test_numbers_and_symbols_are_not_sent(tmp_path):
    assert not TextTools.has_letters("3155") and not TextTools.has_letters("3.200 - [sound:x.mp3]")
    assert TextTools.has_letters("3 gatti")
    cache = TranslationCache(tmp_path / "cache.json", Reporter())
    client = Recorder()
    ft = FieldTranslator(client, CacheView(cache, "rec"), Reporter())
    ft.translate({(1, "F"): ("IT", "PT-BR")}, {(1, "F"): ["3155", "ciao", "3.200"]})
    assert client.sent == ["ciao"]
    assert cache.count() == 1


def test_cached_numbers_are_not_applied(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json", Reporter())
    cache.put("PT-BR", "IT", "3155", "3.155", "deepl")     # left over from an earlier run
    assert CacheView(cache, "deepl").read("PT-BR", "IT", "3155") is None
