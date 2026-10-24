import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from ankitool.core.errors import ConfigError
from ankitool.core.languages import NAMES, TARGETS
from ankitool.core.settings import provider_option
from ankitool.core.text import TextTools, lang_base
from ankitool.providers.base import Language, TranslationProvider, TranslationResult, register_provider
from ankitool.providers.llm import _Retryable, setting

# ankitool uses DeepL-style codes (PT-BR, EN-US...); Azure spells a few of them differently.
# Anything not listed here is just lowercased ("IT" -> "it"). Also the fallback if /languages fails.
_AZURE_CODE = {"PT-BR": "pt", "PT-PT": "pt-pt", "EN-GB": "en", "EN-US": "en",
               "ZH": "zh-Hans", "ZH-HANS": "zh-Hans", "ZH-HANT": "zh-Hant"}
_FROM_AZURE = {"pt": ["PT-BR"], "pt-pt": ["PT-PT"], "en": ["EN-GB", "EN-US"],
               "zh-Hans": ["ZH-HANS"], "zh-Hant": ["ZH-HANT"]}      # Azure code -> ankitool codes
MAX_CHARS = 45_000                             # Azure accepts 50,000 characters per request


def _warn(msg):
    on = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
    print(f"\033[33m{msg}\033[0m" if on else msg, file=sys.stderr, flush=True)


@register_provider
class AzureProvider(TranslationProvider):
    """Azure AI Translator (machine translation, free tier F0). No extra package needed.
    Settings (environment variable or .env): AZURE_KEY (required); AZURE_REGION (needed for regional
    and multi-service resources); AZURE_ENDPOINT (optional, default is the global endpoint)."""

    name, label, key_env = "azure", "Azure Translator", "AZURE_KEY"
    default_endpoint = "https://api.cognitive.microsofttranslator.com"
    batch_size = 40
    max_workers = 2
    timeout = 60
    rate_limit_wait = 20
    WRAP = re.compile(r'<span class="notranslate">(.*?)</span>', re.S)

    def __init__(self, key=None):
        self.chars = 0                         # characters sent in this run (what Azure bills)
        self._codes = dict(_AZURE_CODE)        # ankitool code -> Azure code (extended by fetch_target_languages)
        self._pairs = [(c, n) for c, n in TARGETS]
        self.region = setting("AZURE_REGION") or provider_option(self.name, "region") or ""
        self.endpoint = (setting("AZURE_ENDPOINT") or provider_option(self.name, "endpoint")
                         or self.default_endpoint).rstrip("/")
        if not self.endpoint.startswith("https://"):
            raise ConfigError(f"AZURE_ENDPOINT must start with https://: {self.endpoint}")
        if not self.region:
            _warn("Warning: AZURE_REGION is not set. Regional and multi-service Azure resources need it "
                  "(it is shown next to the key in the Azure portal); without it requests usually fail "
                  "with HTTP 401. Add AZURE_REGION=<region> to .env.")
        super().__init__(key)

    def connect(self, key):
        pass                                   # plain HTTP: nothing to create

    def fetch_target_languages(self):
        """Azure's own list (public endpoint, no key); the built-in table if it cannot be reached."""
        try:
            req = urllib.request.Request(f"{self.endpoint}/languages?api-version=3.0&scope=translation",
                                         headers={"Accept-Language": "en", "User-Agent": "ankitool"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                found = json.load(r)["translation"]
            pairs = []
            for azure, info in found.items():
                for ours in _FROM_AZURE.get(azure, [azure.upper()]):
                    pairs.append((ours, info["name"] if azure not in _FROM_AZURE else dict(TARGETS)[ours]))
                    self._codes[ours] = azure
                self._codes[azure.upper()] = azure
            if pairs:
                self._pairs = sorted(pairs)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass                               # keep the built-in table
        return [Language(code, name) for code, name in self._pairs]

    def fetch_source_languages(self):
        names = {}
        for code, name in self._pairs:
            names.setdefault(lang_base(code), NAMES.get(lang_base(code)) or re.sub(r"\s*\(.*\)", "", name))
        return [Language(code, name) for code, name in sorted(names.items())]

    def _code(self, c):
        return self._codes.get(c.upper(), c.lower())

    def usage(self):
        return f"{self.chars} characters sent in this run"

    # Azure's documented way to skip a piece of HTML is class="notranslate".
    def mask(self, t):
        return TextTools.PROT.sub(lambda m: f'<span class="notranslate">{m.group(0)}</span>', t)

    def unmask(self, t):
        return self.WRAP.sub(r"\1", t)

    @staticmethod
    def _chunks(texts):
        chunk, size = [], 0
        for t in texts:
            if chunk and size + len(t) > MAX_CHARS:
                yield chunk
                chunk, size = [], 0
            chunk.append(t)
            size += len(t)
        if chunk:
            yield chunk

    def _post(self, texts, target, source, html):
        query = {"api-version": "3.0", "to": self._code(target)}
        if source:
            query["from"] = self._code(lang_base(source))
        if html:
            query["textType"] = "html"
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json; charset=UTF-8",
                   "User-Agent": "ankitool"}
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        req = urllib.request.Request(f"{self.endpoint}/translate?{urllib.parse.urlencode(query)}",
                                     json.dumps([{"Text": t} for t in texts]).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = min(max(1, round(float(e.headers.get("Retry-After", "")))), 120)
                except ValueError:
                    wait = self.rate_limit_wait
                raise _Retryable("Azure rate limit reached (HTTP 429)", wait) from e
            if e.code >= 500:
                raise _Retryable(f"Azure server error (HTTP {e.code})") from e
            detail = e.read()[:200].decode("utf-8", "replace") if e.fp else ""
            hint = " AZURE_REGION is not set." if not self.region and e.code in (401, 403) else ""
            raise ConfigError(f"Azure rejected the request (HTTP {e.code}). Check AZURE_KEY, AZURE_REGION "
                              f"and your quota.{hint} {detail}") from e
        except OSError as e:                   # URLError, timeouts, connection errors
            raise _Retryable(f"network error talking to Azure: {e}") from e

    def _translate(self, texts, target_lang, source_lang, html):
        out = []
        for chunk in self._chunks(texts):
            data = self._post(chunk, target_lang, source_lang, html)
            try:
                for item in data:
                    det = (item.get("detectedLanguage") or {}).get("language")
                    out.append(TranslationResult(item["translations"][0]["text"], lang_base(det) or None))
            except (KeyError, IndexError, TypeError, AttributeError) as e:
                raise ConfigError("Azure returned an unexpected reply format") from e
            self.chars += sum(len(t) for t in chunk)
        if len(out) != len(texts):
            raise ConfigError(f"Azure returned {len(out)} translations for {len(texts)} texts")
        return out