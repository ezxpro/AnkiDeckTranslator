import json
import re
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr

from ankitool.core.errors import ConfigError
from ankitool.core.settings import provider_option
from ankitool.providers.llm import _Retryable, setting
from ankitool.tts.base import SpeechProvider, Voice, register_speech

# Real-time REST limits: F0 allows 20 requests per 60 s (not adjustable); S0 starts at 30 per second.
TIER_RPM = {"F0": 18, "S0": 600}               # paced a little under the documented limit
TIER_WORKERS = {"F0": 1, "S0": 4}
MIN_AUDIO_BYTES = 64                           # anything smaller is an error body, not audio


@register_speech
class AzureSpeechProvider(SpeechProvider):
    """Azure AI Speech, text to speech over the REST API. No extra package needed.
    Settings (environment variable or .env):
      AZURE_SPEECH_KEY     (required; AZURE_KEY is used if it is a multi-service resource key)
      AZURE_SPEECH_REGION  (required, e.g. brazilsouth; AZURE_REGION is the fallback)
      AZURE_SPEECH_TIER    F0 (free, default) or S0 (paid): sets the request pacing"""

    name, label, key_env = "azure", "Azure Speech", "AZURE_SPEECH_KEY"
    config_name = "azure-speech"               # config.json: providers.azure-speech.{max_workers,rpm,region,tier}
    output_format = "audio-24khz-48kbitrate-mono-mp3"
    timeout = 60
    rate_limit_wait = 20
    DEFAULT_LOCALE = {"IT": "it-IT", "EN": "en-US", "PT": "pt-BR", "ES": "es-ES", "FR": "fr-FR",
                      "DE": "de-DE", "NL": "nl-NL", "PL": "pl-PL", "SV": "sv-SE", "DA": "da-DK",
                      "NB": "nb-NO", "FI": "fi-FI", "CS": "cs-CZ", "EL": "el-GR", "RO": "ro-RO",
                      "HU": "hu-HU", "TR": "tr-TR", "RU": "ru-RU", "UK": "uk-UA", "JA": "ja-JP",
                      "KO": "ko-KR", "ZH": "zh-CN", "AR": "ar-SA", "HE": "he-IL", "HI": "hi-IN",
                      "ID": "id-ID", "VI": "vi-VN", "TH": "th-TH"}
    PREFERRED = {"it-IT": "it-IT-ElsaNeural", "en-US": "en-US-JennyNeural",
                 "pt-BR": "pt-BR-FranciscaNeural", "es-ES": "es-ES-ElviraNeural",
                 "fr-FR": "fr-FR-DeniseNeural", "de-DE": "de-DE-KatjaNeural",
                 "ja-JP": "ja-JP-NanamiNeural", "zh-CN": "zh-CN-XiaoxiaoNeural",
                 "ko-KR": "ko-KR-SunHiNeural", "ru-RU": "ru-RU-SvetlanaNeural"}

    def __init__(self, key=None):
        self.region = (setting("AZURE_SPEECH_REGION") or setting("AZURE_REGION")
                       or provider_option(self.config_name, "region") or "")
        if not self.region:
            raise ConfigError("Azure Speech needs a region (it is shown next to the key in the Azure "
                              "portal). Add AZURE_SPEECH_REGION=<region> to .env, e.g. brazilsouth.")
        if not re.fullmatch(r"[a-z0-9-]+", self.region):       # it becomes part of the host name
            raise ConfigError(f"AZURE_SPEECH_REGION is not a valid region name: {self.region}")
        self.tier = (setting("AZURE_SPEECH_TIER") or provider_option(self.config_name, "tier") or "F0").upper()
        if self.tier not in TIER_RPM:
            raise ConfigError(f"AZURE_SPEECH_TIER must be one of {', '.join(TIER_RPM)}: {self.tier}")
        self.rpm = TIER_RPM[self.tier]
        self.max_workers = TIER_WORKERS[self.tier]
        self.base = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices"
        super().__init__(key)

    def _load_key(self):
        key = setting("AZURE_SPEECH_KEY") or setting("AZURE_KEY")
        if not key:
            raise ConfigError("API key for Azure Speech not found. Set AZURE_SPEECH_KEY (a Speech or "
                              "multi-service resource key) in the environment or in .env.")
        return key

    def usage(self):
        msg = f"{self.chars} characters synthesized in this run"
        if self.tier == "F0":
            msg += " (the F0 monthly free quota is in the Azure portal; earlier runs are not counted)"
        return msg

    def _fetch_voices(self):
        req = urllib.request.Request(f"{self.base}/voices/list", headers={
            "Ocp-Apim-Subscription-Key": self.key, "User-Agent": "ankitool"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
            return [Voice(v["ShortName"], v["Locale"], v.get("Gender", "")) for v in data
                    if v.get("Status", "GA") != "Deprecated"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []                          # resolve_voice falls back to its built-in table

    def _synthesize(self, text, voice):
        ssml = (f"<speak version='1.0' xml:lang={quoteattr(voice.locale)}>"
                f"<voice name={quoteattr(voice.name)}>{escape(text)}</voice></speak>")
        req = urllib.request.Request(f"{self.base}/v1", ssml.encode("utf-8"), {
            "Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.output_format, "User-Agent": "ankitool"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = min(max(1, round(float(e.headers.get("Retry-After", "")))), 120)
                except ValueError:
                    wait = self.rate_limit_wait
                self._pause(wait)
                raise _Retryable("Azure Speech rate limit reached (HTTP 429)", wait) from e
            if e.code >= 500:
                raise _Retryable(f"Azure Speech server error (HTTP {e.code})") from e
            detail = e.read()[:200].decode("utf-8", "replace") if e.fp else ""
            hint = (" Check the key and that it matches the region (a Translator-only key does not "
                    "work for Speech).") if e.code in (401, 403) else ""
            raise ConfigError(f"Azure Speech rejected the request (HTTP {e.code}).{hint} {detail}") from e
        except OSError as e:                   # URLError, timeouts, connection errors
            raise _Retryable(f"network error talking to Azure Speech: {e}") from e
        if len(data) < MIN_AUDIO_BYTES:
            raise _Retryable("Azure Speech returned no audio")
        with self._lock:
            self.chars += len(text)
        return data
