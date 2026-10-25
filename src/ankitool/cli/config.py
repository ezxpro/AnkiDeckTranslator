import json

from ankitool.core.errors import ConfigError
from ankitool.cache.translation import MODES
from ankitool.core.settings import PROVIDER_KEYS, get_settings
from ankitool.providers.base import PROVIDERS


class ConfigApp:
    """config command: show / set / unset the preferences saved in config.json."""

    def __init__(self, opts):
        self.opts = opts

    @staticmethod
    def _keys(key):
        if not key:
            raise ConfigError("Give a KEY: provider, or SERVICE.model / SERVICE.base_url / SERVICE.max_workers / SERVICE.rpm / SERVICE.cache")
        if key == "provider":
            return ("provider",)
        name, _, opt = key.partition(".")
        if name not in PROVIDERS or opt not in PROVIDER_KEYS:
            raise ConfigError(f"Unknown key '{key}'. Use: provider, or "
                              f"<{'|'.join(sorted(PROVIDERS))}>.<{'|'.join(PROVIDER_KEYS)}>")
        return ("providers", name, opt)

    @staticmethod
    def _value(keys, value):
        if keys == ("provider",):
            if value not in PROVIDERS:
                raise ConfigError(f"Unknown service '{value}'. Available: {', '.join(sorted(PROVIDERS))}")
            return value
        if keys[2] == "cache" and value not in MODES:
            raise ConfigError(f"cache must be one of: {', '.join(MODES)} (shared = reuse any service's "
                              f"translation; isolated = only this service's)")
        if PROVIDER_KEYS[keys[2]] is int:
            if not value.isdigit() or int(value) < 1:
                raise ConfigError(f"{keys[2]} must be an integer greater than zero")
            return int(value)
        return value

    def run(self):
        o, st = self.opts, get_settings()
        if o.action == "show":
            print("Saved preferences (non-secret settings).")
            print("API keys live in .env, not here — see '.env.example'.")
            print("Command:  ankitool config show | set <KEY> <VALUE> | unset <KEY>")
            print("  Keys:   provider, or SERVICE.model / SERVICE.base_url / SERVICE.max_workers / SERVICE.rpm / SERVICE.cache")
            print("  Example: ankitool config set provider groq")
            print("           ankitool config set groq.max_workers 3")
            print()
            print(f"Settings file: {st.path}" + ("" if st.path.exists() else " (not created yet)"))
            print(json.dumps(st.data, indent=2, ensure_ascii=False) if st.data
                  else "(empty: built-in defaults)")
            return
        if st.error:
            raise ConfigError(st.error)                # do not overwrite a file we could not read
        keys = self._keys(o.key)
        if o.action == "set":
            if o.value is None:
                raise ConfigError("'set' needs a VALUE")
            st.set(self._value(keys, o.value), *keys)
        else:
            st.unset(*keys)
        st.save()
        print(f"Saved to {st.path}")
