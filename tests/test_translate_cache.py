from ankitool.cache.translation import TranslationCache
from ankitool.core.text import TextTools
from ankitool.core.progress import Reporter
from ankitool.ops.translate import FieldTranslator
from ankitool.providers.base import TranslationResult


class StubClient:
    """Mimics the provider contract: masks the tags, translates, and unmasks the result."""
    label = "stub"
    batch_size = 40
    max_workers = 1

    def __init__(self, reply):
        self.reply = reply

    def mask(self, t):
        return TextTools.mask(t)

    def translate(self, texts, target_lang, source_lang=None, protect_tags=False):
        return [TranslationResult(TextTools.unmask(self.reply(t))) for t in texts]


SRC = "ciao [sound:a.mp3]"
KEY = ((1, "Front"), ("IT", "PT-BR"))


def _run(tmp_path, reply):
    cache = TranslationCache(tmp_path / "c.json", {"PT-BR"})
    FieldTranslator(StubClient(reply), cache, Reporter()).translate(
        {KEY[0]: KEY[1]}, {KEY[0]: [SRC]})
    return cache


def test_damaged_tags_are_not_cached(tmp_path):
    # reply that drops the [sound:...] must not be stored as a translation
    cache = _run(tmp_path, lambda t: "olá")
    assert cache.get("PT-BR", "IT", SRC) is None


def test_intact_tags_are_cached(tmp_path):
    cache = _run(tmp_path, lambda t: t.replace("ciao", "olá"))
    assert cache.get("PT-BR", "IT", SRC) == "olá [sound:a.mp3]"
