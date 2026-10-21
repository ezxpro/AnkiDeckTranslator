import re

from ankitool.core.text import TextTools
from ankitool.providers.base import Language, TranslationProvider, TranslationResult, register_provider


@register_provider
class FakeProvider(TranslationProvider):
    """Fake service, for testing the program without spending any service characters.
    Usage: --provider fake. No key or internet needed. The "translation" only puts the language
    code in front of the text, and the language is guessed from a minimal list of common words
    (good for testing the flow, not the quality). usage() shows how many characters this run
    WOULD have spent on a real service."""

    name, label, key_env = "fake", "Fake (test, no cost)", "FAKE_KEY"
    hidden = True
    STOP = {"IT": {"il", "lo", "la", "gli", "le", "di", "che", "è", "per", "un", "una", "non",
                   "sono", "del", "della", "mi", "ho", "con", "ma", "più", "vado", "poi",
                   "al", "dal", "nel", "questo", "fa", "viene"},
            "EN": {"the", "of", "and", "to", "is", "in", "you", "he", "she", "it", "for",
                   "with", "was", "are", "my", "his", "her", "this", "that", "on", "have"},
            "PT": {"o", "os", "as", "da", "do", "uma", "não", "são", "um", "para", "com",
                   "você", "eu", "ele", "ela", "está", "muito", "mais", "isso", "que"}}

    def __init__(self):
        self.chars = 0                                   # characters "spent" in this run
        super().__init__(key="fake")

    def connect(self, key):
        pass

    def fetch_target_languages(self):
        return [Language(c, n) for c, n in (("PT-BR", "Portuguese (Brazilian)"),
                ("EN-US", "English (American)"), ("IT", "Italian"), ("ES", "Spanish"))]

    def fetch_source_languages(self):
        return [Language(c, n) for c, n in (("PT", "Portuguese"), ("EN", "English"),
                ("IT", "Italian"), ("ES", "Spanish"))]

    def _guess(self, text):
        words = set(re.findall(r"\w+", TextTools.plain(text).lower()))
        score = {lang: len(words & stop) for lang, stop in self.STOP.items()}
        best = max(score, key=score.get)
        # No stopword matched (e.g. numbers/unknown text): default to English.
        return best if score[best] else "EN"

    def usage(self):
        return f"{self.chars} simulated characters"

    def detect(self, texts, target_lang):
        self.chars += sum(len(t) for t in texts)
        return [self._guess(t) for t in texts]

    def _translate(self, texts, target_lang, source_lang, html):
        self.chars += sum(len(t) for t in texts)
        return [TranslationResult(f"[{target_lang}] {t}", self._guess(t)) for t in texts]
