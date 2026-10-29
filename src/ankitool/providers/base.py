from abc import ABC, abstractmethod
from dataclasses import dataclass
import time, threading
from ankitool.core.settings import env_value

from ankitool.core.errors import Cancelled, ConfigError
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

class RetryPacing:
    """Retries, request pacing (rpm) and cancellable waits. Shared by the translation services
    and the speech services (ankitool.tts), so both react the same way to rate limits."""

    retries = 3
    max_rate_waits = 10                        # waits after a rate limit (don't use up `retries`)
    rpm = None                                 # max requests per minute (None = no pacing)
    transient_errors = (ConnectionError, TimeoutError)   # errors worth retrying
    on_wait = None                             # optional callback(seconds, reason) before a retry sleep
    cancel_event = None                        # threading.Event set on cancel; wakes sleeping waits

    def _init_pacing(self):
        self._lock = threading.Lock()          # shared by all workers (pacing + counters)
        self._next_slot = 0.0                  # time.monotonic() when the next call may start

    def _retrying(self, call):
        """Runs call() with the retry budgets (real failures vs rate limits); None if it never succeeds."""
        failures = limited = 0
        while True:
            try:
                return call()
            except self.transient_errors as e:
                if getattr(e, "wait", None):               # rate limit: its own budget
                    limited += 1
                    if limited > self.max_rate_waits:
                        return None
                else:
                    failures += 1
                    if failures >= self.retries:
                        return None
                self.wait(e)

    def _sleep(self, seconds):
        """Sleeps, but wakes up at once (raising Cancelled) if the operation is cancelled."""
        if self.cancel_event is None:
            time.sleep(seconds)
        elif self.cancel_event.wait(seconds):
            raise Cancelled("Cancelled.")

    def _throttle(self, cost=0.0):
        """Reserves a slot in the shared schedule and sleeps until it starts.
        cost = seconds this call occupies (e.g. chars / rate); rpm sets a minimum."""
        cost = max(cost, 60 / self.rpm if self.rpm else 0)
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + cost
        if start > now:
            self._sleep(start - now)

    def _pause(self, seconds):
        """After a rate limit, ALL workers wait, not only the one that received it."""
        with self._lock:
            self._next_slot = max(self._next_slot, time.monotonic() + seconds)

    def wait(self, error):
        """Sleeps before a retry (the error may carry its own `wait` seconds) and reports it."""
        seconds = getattr(error, "wait", None) or 3
        self._pause(seconds)
        if self.on_wait:
            self.on_wait(seconds, str(error) or type(error).__name__)
        time.sleep(seconds)


class TranslationProvider(RetryPacing, ABC):
    """The contract of a translation service. The rest of the program only knows this interface.

    To add another service (Google, Azure, LibreTranslate, ...):
      1. create a subclass with name, label and key_env;
      2. implement connect, fetch_target_languages, fetch_source_languages and _translate
         (and, if available, usage and detect);
      3. decorate the class with @register_provider.
    It then works with --provider and in the interactive menu, with its own cache
    in the shared cache.sqlite, under its name. If the service does not understand HTML with translate="no",
    override mask/unmask to protect [sound:...] and {{c1::...}} in another way."""

    name = ""                                  # identifier used in --provider
    variant = ""                                # output-affecting settings (model...); "" if none
    label = ""                                 # display name
    key_env = ""                               # environment variable / .env key name
    default_target = "PT-BR"                   # suggested output language code
    batch_size = 40                            # texts per call
    max_workers = 1                            # parallel calls; raise per provider if its limits allow
    hidden = False                             # True: only available with --provider, not in the menu

    def __init__(self, key=None):
        self._init_pacing()
        self.key = key or load_secret(self.key_env, self.label)
        self.connect(self.key)
        self.target_langs = self.fetch_target_languages()
        self.source_langs = self.fetch_source_languages()
        self.target_codes = {l.code for l in self.target_langs}
        self.source_names = {l.code: l.name for l in self.source_langs}
        self._target_by_norm = {l.code.upper(): l.code for l in self.target_langs}
        self._target_by_name = {l.name.upper(): l.code for l in self.target_langs}
        workers = get_settings().get("providers", self.name, "max_workers")   # config.json override
        rpm = get_settings().get("providers", self.name, "rpm")   # config.json override
        size = get_settings().get("providers", self.name, "batch_size")   # config.json override
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:
            self.batch_size = size
        if isinstance(rpm, int) and not isinstance(rpm, bool) and rpm >= 0:
            self.rpm = rpm or None                                # 0 = no pacing
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
        res = self._retrying(lambda: self._translate(payload, target_lang, source_lang, protect_tags))
        if res is None:
            return None
        if protect_tags:
            res = [TranslationResult(self.unmask(r.text), r.detected_source_lang) for r in res]
        return res

    def resolve_target(self, raw):
        """Typed text -> valid output code (in the service's spelling), or None.
        Accepts the code or the language name in any case ('pt-br', 'French', 'portuguese (brazilian)'),
        or the start of a name that is unique ('germ' -> German)."""
        s = " ".join((raw or "").split()).upper()
        if not s:
            return None
        hits = {c for n, c in self._target_by_name.items() if n.startswith(s)}
        return (self._target_by_norm.get(s) or self._target_by_name.get(s)
                or (next(iter(hits)) if len(hits) == 1 else None))