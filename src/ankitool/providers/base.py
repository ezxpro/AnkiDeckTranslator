from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import time
from ankitool.core.settings import env_value

from ankitool.core.errors import ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.settings import get_settings
from ankitool.core.text import TextTools


@dataclass(frozen=True)
class Language:
    code: str                                  # code in the provider's own spelling
    name: str

@dataclass(frozen=True)
class TranslationResult:
    text: str
    detected_source_lang: str = None           # detected source language (provider's spelling)

def load_secret(env_name, label):
    """Looks for the key in the environment variable <env_name>, then in the .env file next to
    the script. Inline '# comments' are removed the same way as for other settings."""
    key = env_value(env_name)
    if not key:
        raise ConfigError(f"API key for {label} not found. Set the environment variable "
                          f"{env_name} or create a .env file next to the script with:\n"
                          f"{env_name}=your-key-here")
    return key

PROVIDERS = {}                                 # name -> class; filled by @register_provider

def register_provider(cls):
    PROVIDERS[cls.name] = cls
    return cls

def make_provider(name):
    if name not in PROVIDERS:
        raise ConfigError(f"Unknown translation service: {name}. "
                          f"Available: {', '.join(sorted(PROVIDERS))}")
    return PROVIDERS[name]()

class TranslationProvider(ABC):
    """The contract of a translation service. The rest of the program only knows this interface.

    To add another service (Google, Azure, LibreTranslate, ...):
      1. create a subclass with name, label and key_env;
      2. implement connect, fetch_target_languages, fetch_source_languages and _translate
         (and, if available, usage and detect);
      3. decorate the class with @register_provider.
    It then works with --provider and in the interactive menu, with its own cache
    (cache_<name>.json). If the service does not understand HTML with translate="no",
    override mask/unmask to protect [sound:...] and {{c1::...}} in another way."""

    name = ""                                  # identifier used in --provider
    label = ""                                 # display name
    key_env = ""                               # environment variable / .env key name
    default_target = "PT-BR"                   # suggested output language code
    batch_size = 40                            # texts per call
    retries = 3
    max_workers = 1                            # parallel calls; raise per provider if its limits allow
    transient_errors = (ConnectionError, TimeoutError)   # errors worth retrying
    cache_tag = None                           # None -> "_<name>" in cache file names
    hidden = False                             # True: only available with --provider, not in the menu
    on_wait = None                             # optional callback(seconds, reason) before a retry sleep

    def __init__(self, key=None):
        self.key = key or load_secret(self.key_env, self.label)
        self.connect(self.key)
        self.target_langs = self.fetch_target_languages()
        self.source_langs = self.fetch_source_languages()
        self.target_codes = {l.code for l in self.target_langs}
        self.source_names = {l.code: l.name for l in self.source_langs}
        self._target_by_norm = {l.code.upper(): l.code for l in self.target_langs}
        workers = get_settings().get("providers", self.name, "max_workers")   # config.json override
        if isinstance(workers, int) and not isinstance(workers, bool) and workers > 0:
            self.max_workers = workers

    # ---- to implement in each service ----
    @abstractmethod
    def connect(self, key):
        """Creates the service client (and sets transient_errors, if needed)."""

    @abstractmethod
    def fetch_target_languages(self):
        """-> list of Language available as output."""

    @abstractmethod
    def fetch_source_languages(self):
        """-> list of Language recognized as source."""

    @abstractmethod
    def _translate(self, texts, target_lang, source_lang, html):
        """One call to the service. Returns one TranslationResult per text, in the same order.
        html=True: the texts contain <span translate="no"> tags that must not be translated."""

    def usage(self):
        """Current usage (for display), or None if the service does not report it."""
        return None

    def detect(self, texts, target_lang):
        """Language of each text (None if the service is unavailable). By default this translates
        and reads the detected language; override it if the service has a detection endpoint."""
        res = self.translate(texts, target_lang)
        return None if res is None else [r.detected_source_lang for r in res]

    # ---- common to all ----
    def mask(self, t):
        return TextTools.mask(t)

    def unmask(self, t):
        return TextTools.unmask(t)

    def translate(self, texts, target_lang, source_lang=None, protect_tags=False):
        """Translates with retries; returns None if the service does not respond.
        protect_tags=True protects [sound:...] and {{c1::...}} (restored in the result)."""
        payload = [self.mask(t) for t in texts] if protect_tags else list(texts)
        for attempt in range(self.retries):
            try:
                res = self._translate(payload, target_lang, source_lang, protect_tags)
                break
            except self.transient_errors as e:
                if attempt < self.retries - 1:
                    self.wait(e)
        else:
            return None
        if protect_tags:
            res = [TranslationResult(self.unmask(r.text), r.detected_source_lang) for r in res]
        return res

    def wait(self, error):
        """Sleeps before a retry (the error may carry its own `wait` seconds) and reports it."""
        seconds = getattr(error, "wait", None) or 3
        if self.on_wait:
            self.on_wait(seconds, str(error) or type(error).__name__)
        time.sleep(seconds)

    def resolve_target(self, raw):
        """Typed text -> valid output code (in the service's spelling), or None."""
        return self._target_by_norm.get((raw or "").strip().upper())

    def cache_file(self, stem, cache_dir):
        tag = f"_{self.name}" if self.cache_tag is None else self.cache_tag
        return Path(cache_dir) / f"{stem}{tag}.json"
