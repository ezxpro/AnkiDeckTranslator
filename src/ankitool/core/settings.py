"""User preferences (NO secrets) in config.json next to .env. Shared by the CLI and a future GUI.

Layout:  {"provider": "groq", "providers": {"groq": {"model": "...", "base_url": "...", "max_workers": 3}}}
Lookup order for each value: command-line flag > environment variable > config.json > built-in default.
API keys stay in .env / environment variables, never here."""
import json
import os
import sys
from pathlib import Path

from ankitool.core.paths import SCRIPT_DIR

CONFIG_FILE = SCRIPT_DIR / "config.json"
PROVIDER_KEYS = {"model": str, "base_url": str, "max_workers": int, "rpm": int, "batch_size": int, "cache": str}   # per-provider options


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else CONFIG_FILE
        self.data, self.error = {}, None
        self.load()

    def load(self):
        self.data, self.error = {}, None
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("the top level must be an object")
            self.data = data
        except (OSError, ValueError) as e:
            self.error = f"{self.path.name} ignored (invalid: {e}). Fix or delete it."

    def get(self, *keys, default=None):
        cur = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def set(self, value, *keys):
        cur = self.data
        for k in keys[:-1]:
            if not isinstance(cur.get(k), dict):
                cur[k] = {}
            cur = cur[k]
        cur[keys[-1]] = value

    def unset(self, *keys):
        cur = self.data
        for k in keys[:-1]:
            cur = cur.get(k)
            if not isinstance(cur, dict):
                return
        cur.pop(keys[-1], None)

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)                     # never leaves a half-written file


def _read_env_file(env_file, name):
    """Value of `name` in a NAME=value file (inline '# comments' removed), or \"\" if absent."""
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.removeprefix("export ").strip() == name:
                return v.split(" #", 1)[0].strip().strip('"').strip("'")
    return ""


def env_value(name, env_file=None):
    """Value of `name` from the environment, else from the .env file next to the script (\"\" if absent)."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return _read_env_file(Path(env_file) if env_file else SCRIPT_DIR / ".env", name)


_instance = None

def get_settings():
    """The shared Settings (loaded once); warns once if the file is invalid."""
    global _instance
    if _instance is None:
        _instance = Settings()
        if _instance.error:
            print(f"[Warning] {_instance.error}", file=sys.stderr)
    return _instance

def configured_provider():
    """Service chosen by the user (ANKITOOL_PROVIDER in env/.env, or config.json), or None."""
    return env_value("ANKITOOL_PROVIDER") or get_settings().get("provider") or None

def provider_option(provider, key):
    """A provider option from config.json (model, base_url, max_workers), or None."""
    return get_settings().get("providers", provider, key) or None
