from abc import ABC, abstractmethod
from dataclasses import dataclass
import re

from ankitool.core.errors import ConfigError
from ankitool.core.settings import get_settings
from ankitool.core.text import lang_base
from ankitool.providers.base import RetryPacing, load_secret

SPEECH = {}                                    # name -> class; filled by @register_speech


def register_speech(cls):
    SPEECH[cls.name] = cls
    return cls


def make_speech(name):
    if name not in SPEECH:
        raise ConfigError(f"Unknown speech service: {name}. Available: {', '.join(sorted(SPEECH))}")
    return SPEECH[name]()


@dataclass(frozen=True)
class Voice:
    name: str                                  # the service's own id, e.g. it-IT-ElsaNeural
    locale: str                                # it-IT
    gender: str = ""


_VOICE_NAME = re.compile(r"^(?P<locale>[a-z]{2,3}-[A-Za-z0-9]+)-[A-Za-z0-9-]+$")


class SpeechProvider(RetryPacing, ABC):
    """The contract of a text-to-speech service (the audio counterpart of TranslationProvider).

    To add another service: subclass it with name, label and key_env; implement _fetch_voices and
    _synthesize (and, if it has them, usage and DEFAULT_LOCALE / PREFERRED); decorate it with
    @register_speech and import the module in tts/__init__.py."""

    name = ""                                  # identifier used in --speech
    label = ""
    key_env = ""
    config_name = ""                           # key under "providers" in config.json (default: name)
    extension = "mp3"                          # file type _synthesize returns
    max_workers = 1
    DEFAULT_LOCALE = {}                        # base language ("IT") -> locale to use ("it-IT")
    PREFERRED = {}                             # locale -> voice name tried first

    def __init__(self, key=None):
        self._init_pacing()
        self._voices = None
        self.chars = 0                         # characters synthesized in this run
        self.key = key or self._load_key()
        cfg = self.config_name or self.name
        for attr in ("max_workers", "rpm"):    # config.json overrides
            v = get_settings().get("providers", cfg, attr)
            if isinstance(v, int) and not isinstance(v, bool) and v >= (1 if attr == "max_workers" else 0):
                setattr(self, attr, v or None)

    def _load_key(self):
        return load_secret(self.key_env, self.label)

    # ---- to implement in each service ----
    @abstractmethod
    def _fetch_voices(self):
        """-> list of Voice the service offers; [] if the list cannot be fetched."""

    @abstractmethod
    def _synthesize(self, text, voice):
        """One call to the service -> audio bytes. Raise _Retryable (rate limit / server / network)
        or ConfigError; may carry `wait`. Must count characters in self.chars."""

    def usage(self):
        return f"{self.chars} characters synthesized in this run"

    # ---- common to all ----
    def voices(self):
        if self._voices is None:
            self._voices = self._fetch_voices()
        return self._voices

    def resolve_voice(self, lang, override=None):
        """Language ('IT') or an explicit voice name -> Voice. ConfigError if none fits."""
        voices = self.voices()
        if override:
            hit = next((v for v in voices if v.name.lower() == override.lower()), None)
            if hit:
                return hit
            m = _VOICE_NAME.match(override)
            if voices or not m:
                raise ConfigError(f"Unknown voice '{override}' for {self.label}. Voice names look like "
                                  f"{next(iter(self.PREFERRED.values()), 'it-IT-ElsaNeural')}.")
            return Voice(override, m["locale"])           # list unavailable: trust the name
        code = lang_base(lang)
        locale = self.DEFAULT_LOCALE.get(code)
        pool = [v for v in voices if v.locale == locale] if locale else []
        pool = pool or [v for v in voices if v.locale.upper().startswith(code + "-")]
        pref = self.PREFERRED.get(locale)
        if pref:
            hit = next((v for v in pool if v.name == pref), None)
            if hit:
                return hit
            if not voices:                                # list unavailable: trust the table
                return Voice(pref, locale)
        if pool:
            return sorted(pool, key=lambda v: v.name)[0]
        raise ConfigError(f"{self.label} has no voice for language {code}. "
                          f"Choose one with --voice NAME.")

    def voices_for(self, lang):
        """Voices that speak `lang` (e.g. 'PT'): the default locale first (pt-BR), then other
        locales (pt-PT), the preferred voice at the top of each group. [] if no list."""
        code = lang_base(lang)
        default = self.DEFAULT_LOCALE.get(code)
        pref = self.PREFERRED.get(default)
        pool = [v for v in self.voices() if v.locale.upper().startswith(code + "-")]
        return sorted(pool, key=lambda v: (v.locale != default, v.locale, v.name != pref, v.name))   

    def synthesize(self, text, voice):
        """Audio bytes with retries and pacing; None if the service never responds."""
        def call():
            self._throttle()
            return self._synthesize(text, voice)
        return self._retrying(call)
