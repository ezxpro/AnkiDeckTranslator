import json
import urllib.error
import urllib.request

from ankitool.core.errors import ConfigError
from ankitool.core.settings import provider_option
from ankitool.providers.base import register_provider
from ankitool.providers.llm import LLMProvider, _BadReply, _Retryable, setting


@register_provider
class OpenAICompatProvider(LLMProvider):
    """Any server with the OpenAI chat-completions API (OpenAI, Groq, Cerebras, OpenRouter, Ollama...).
    No extra package needed. Settings (environment variable or .env), with <P> = key_env minus "_KEY":
      <P>_KEY (required), <P>_BASE_URL and <P>_MODEL (optional, override the class defaults).
    To add a preset for another service, subclass it and set name, label, key_env and the defaults."""

    name, label, key_env = "openai", "OpenAI-compatible", "OPENAI_KEY"
    default_base_url, default_model = "https://api.openai.com/v1", "gpt-4o-mini"
    max_workers = 2
    timeout = 60                                # seconds per request

    def __init__(self, key=None):
        prefix = self.key_env.removesuffix("_KEY")
        self.base_url = (setting(f"{prefix}_BASE_URL") or provider_option(self.name, "base_url")
                         or self.default_base_url).rstrip("/")
        self.model = setting(f"{prefix}_MODEL") or provider_option(self.name, "model") or self.default_model
        super().__init__(key)

    def connect(self, key):
        pass                                    # plain HTTP: nothing to create

    def _generate(self, system, payload):
        body = json.dumps({"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": payload}]}).encode()
        req = urllib.request.Request(f"{self.base_url}/chat/completions", body, {
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}",
            "User-Agent": "ankitool"})          # some hosts (Cloudflare) reject Python's default
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = min(max(1, round(float(e.headers.get("Retry-After", "")))), 120)
                except ValueError:
                    wait = self.rate_limit_wait
                raise _Retryable(f"{self.label} rate limit reached (HTTP 429)", wait) from e
            if e.code >= 500:
                raise _Retryable(f"{self.label} server error (HTTP {e.code})") from e
            detail = e.read()[:200].decode("utf-8", "replace") if e.fp else ""
            raise ConfigError(f"{self.label} rejected the request (HTTP {e.code}): {detail}") from e
        except OSError as e:                    # URLError, timeouts, connection errors
            raise _Retryable(f"network error talking to {self.label}: {e}") from e
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise _BadReply("unexpected reply format") from e
        if not text:
            raise _BadReply("empty reply")
        return text


@register_provider
class GroqProvider(OpenAICompatProvider):
    """Groq (free tier: generous daily limits). Key: GROQ_KEY; optional GROQ_MODEL / GROQ_BASE_URL."""

    name, label, key_env = "groq", "Groq", "GROQ_KEY"
    default_base_url, default_model = "https://api.groq.com/openai/v1", "llama-3.1-8b-instant"
