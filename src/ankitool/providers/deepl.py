from ankitool.providers.base import Language, TranslationProvider, TranslationResult, register_provider


@register_provider
class DeepLProvider(TranslationProvider):
    name, label, key_env = "deepl", "DeepL", "DEEPL_KEY"

    def connect(self, key):
        import deepl                           # imported here: only DeepL users need it
        self.tr = deepl.Translator(key)
        self.transient_errors = (deepl.ConnectionException,)

    def fetch_target_languages(self):
        return [Language(l.code, l.name) for l in self.tr.get_target_languages()]

    def fetch_source_languages(self):
        return [Language(l.code, l.name) for l in self.tr.get_source_languages()]

    def usage(self):
        return self.tr.get_usage().character

    def _translate(self, texts, target_lang, source_lang, html):
        res = self.tr.translate_text(texts, source_lang=source_lang, target_lang=target_lang,
                                     tag_handling="html" if html else None)
        return [TranslationResult(r.text, r.detected_source_lang) for r in res]
