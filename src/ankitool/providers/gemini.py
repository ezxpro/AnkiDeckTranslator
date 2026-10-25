import time                                    # noqa: F401  (tests patch gemini.time.sleep)
import re

from ankitool.core.errors import ConfigError
from ankitool.core.settings import env_value, provider_option
from ankitool.providers.base import register_provider
from ankitool.providers.llm import LLMProvider, _BadReply, _Retryable   # noqa: F401  (re-exported)

# "-latest" alias so the default does not go stale when Google retires a model name.
# Override with the GEMINI_MODEL environment variable (see the model list in Google AI Studio).
DEFAULT_MODEL = "gemini-flash-lite-latest"


@register_provider
class GeminiProvider(LLMProvider):
    """Google Gemini (LLM) as a translation service; works with the free tier of the Gemini API.

    Needs `pip install google-genai` (extra: .[gemini]) and the key GEMINI_KEY (environment
    variable or .env). Optional: GEMINI_MODEL to choose another model. The free tier has
    per-minute and per-day request limits, so a 429 is waited out; usage() returns None.
    """

    name, label, key_env = "gemini", "Google Gemini", "GEMINI_KEY"
    max_workers = 1
    rpm = 14                                   # free tier is 15 RPM; stay just under

    def __init__(self, key=None):
        self.model = (env_value("GEMINI_MODEL") or provider_option("gemini", "model")
                      or DEFAULT_MODEL)
        super().__init__(key)

    def connect(self, key):
        try:
            from google import genai
        except ImportError as e:
            raise ConfigError("Gemini needs the 'google-genai' package. Install it with: "
                              "pip install google-genai   (or: pip install -e .[gemini])") from e
        self.client = genai.Client(api_key=key)

    def _generate(self, system, payload):
        import httpx
        from google.genai import errors, types
        try:
            reply = self.client.models.generate_content(
                model=self.model, contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=system, temperature=0,
                    response_mime_type="application/json"))
        except errors.APIError as e:
            if e.code == 429:
                msg = str(e.message or "")
                if re.search(r"per[ _-]?day", msg, re.I):
                    raise ConfigError("Gemini daily quota reached. Try again tomorrow, or use another "
                                      "provider (e.g. --provider groq). The cache keeps your progress.") from e
                raise _Retryable("Gemini rate limit reached (HTTP 429)", self.rate_limit_wait) from e
            if e.code in (500, 502, 503, 504):
                raise _Retryable(f"Gemini server error (HTTP {e.code})") from e
            raise ConfigError(f"Gemini rejected the request (HTTP {e.code}): {e.message}") from e
        except httpx.TransportError as e:
            raise _Retryable(f"network error talking to Gemini: {e}") from e
        if not reply.text:
            raise _BadReply("empty reply (blocked or truncated)")
        return reply.text
