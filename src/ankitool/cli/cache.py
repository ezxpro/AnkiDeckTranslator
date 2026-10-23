from pathlib import Path
import json

from ankitool.cache.translation import DEFAULT_MODE, MODES, TranslationCache
from ankitool.cli.console import Ui
from ankitool.core.errors import Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.progress import Reporter
from ankitool.core.settings import get_settings, provider_option
from ankitool.providers.base import PROVIDERS

MODE_HELP = {
    "shared": "reuse any service's translation when this service has none (the default)",
    "provider": "reuse translations from any model of this service; other services are ignored",
    "isolated": "reuse only this service and model; everything else is ignored",
}


def visible_services():
    """Services the user sees (hidden ones, like the test 'fake', only with --provider)."""
    return [n for n, c in PROVIDERS.items() if not c.hidden]


def service_mode(name):
    m = provider_option(name, "cache")
    return m if m in MODES else DEFAULT_MODE


class CacheApp:
    """cache command: show the reuse mode of each service, change it (command line or menu).
    Reads and writes the cache file and config.json only: no API calls, no API key needed."""

    def __init__(self, opts):
        self.opts = opts
        self.cache_dir = Path(opts.cache_dir).expanduser().resolve() if opts.cache_dir else SCRIPT_DIR

    def run(self):
        getattr(self, f"_{self.opts.action or 'show'}")()

    def _cache(self):
        return TranslationCache(self.cache_dir / "cache.sqlite", Reporter())

    def _save_mode(self, service, mode=None, reset=False):
        st = get_settings()
        if st.error:
            raise ConfigError(st.error)                 # do not overwrite a config file we could not read
        if reset:
            st.unset("providers", service, "cache")
        else:
            st.set(mode, "providers", service, "cache")
        st.save()
        print(f"Saved to {st.path}")

    def _show(self):
        cache, stats = self._cache(), self._cache().stats()
        print(f"=== Cache: {self.cache_dir / 'cache.sqlite'} ===")
        print("Mode per service (translate uses it unless --cache-mode is given):")
        for svc in visible_services():
            n = sum(stats.get(svc, {}).values())
            print(f"  {svc:<8} {service_mode(svc):<9} {n} translations stored")
        print(f"Total: {cache.count()} translations, {len(cache.lang)} text languages")
        print("\nChange it:  ankitool cache use SERVICE shared|provider|isolated")
        print("Menu:       ankitool cache select      (choose the service and mode from a list)")

    def _use(self):
        o = self.opts
        if o.reset and o.mode:
            raise ConfigError("use either MODE or --reset, not both. Example: ankitool cache use deepl --reset")
        if not o.service:
            return self._show()
        if o.reset:
            self._save_mode(o.service, reset=True)
        elif o.mode:
            self._save_mode(o.service, o.mode)
        else:
            print(f"{o.service}: {service_mode(o.service)}  ({MODE_HELP[service_mode(o.service)]})")
            print(f"Change it: ankitool cache use {o.service} shared|provider|isolated")
            return
        print(f"{o.service}: now {service_mode(o.service) if not o.reset else DEFAULT_MODE} "
              f"({MODE_HELP[DEFAULT_MODE if o.reset else o.mode]})")

    def _invalidate(self):
        o = self.opts
        if o.all and any([o.provider, o.target, o.lang, o.text]):
            raise ConfigError("--all cannot be combined with --provider, --target, --lang or --text")
        if not o.all and not any([o.provider, o.target, o.lang, o.text]):
            raise ConfigError("invalidate needs a filter (--provider, --target, --lang or --text), "
                              "or --all. Example: ankitool cache invalidate -p gemini")
        if o.all:
            question = "Delete all provider translations and detected languages? Manual corrections are kept."
        else:
            parts = [f"{k}={v}" for k, v in (("service", o.provider), ("target", o.target),
                                             ("language", o.lang), ("text", o.text)) if v]
            question = f"Delete the translations matching {', '.join(parts)}?"
        if not Ui.auto and not Ui.yes(question, "n"):
            raise Cancelled("Cancelled. Nothing was deleted.")
        cache = self._cache()
        if o.all:
            tr, lang = cache.clear_all()
            print(f"Removed {tr} translations and {lang} text languages. Manual corrections were kept.")
        else:
            tr = cache.invalidate(service=o.provider, target=o.target.upper() if o.target else None,
                                  lang=o.lang.upper() if o.lang else None, text=o.text)
            print(f"Removed {tr} translations.")
        cache.save()
        cache.close()

    def _export(self):
        o = self.opts
        if not (self.cache_dir / "cache.sqlite").exists():
            raise ConfigError(f"No cache file yet in {self.cache_dir}. Translate something first.")
        path = Path(o.file).expanduser().resolve()
        if path.exists() and not Ui.auto and not Ui.yes(f"Replace {path.name}?", "n"):
            raise Cancelled("Cancelled. Nothing was written.")
        cache = self._cache()
        rows = cache.export_rows()
        cache.close()
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Exported {len(rows)} rows to {path}")    

    def _select(self):
        if Ui.auto:
            raise ConfigError("'cache select' needs an interactive terminal. "
                              "Without one, use: ankitool cache use SERVICE shared|isolated")
        services = visible_services()
        choices = {str(i): (n, f"currently: {service_mode(n)}") for i, n in enumerate(services, 1)}
        choices["0"] = ("Back", "Change nothing.")
        svc_key = Ui.choose("Which service do you want to change?", choices, "1")
        if svc_key == "0":
            raise Cancelled("Nothing changed.")
        svc = services[int(svc_key) - 1]
        modes = {str(i): (m, MODE_HELP[m]) for i, m in enumerate(MODES, 1)}
        modes["0"] = ("Back", "Change nothing.")
        mode_key = Ui.choose(f"How should {svc} reuse translations? (now: {service_mode(svc)})", modes,
                             str(MODES.index(service_mode(svc)) + 1))
        if mode_key == "0":
            raise Cancelled("Nothing changed.")
        self._save_mode(svc, MODES[int(mode_key) - 1])
        print(f"{svc}: now {MODES[int(mode_key) - 1]}.")
