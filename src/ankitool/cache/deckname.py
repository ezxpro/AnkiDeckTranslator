import json

from ankitool.core.progress import Reporter


class DeckNameCache:
    """deck_cache.json, separate from cache.json. Only the raw service output is stored;
    names typed by hand are never written to the cache."""

    def __init__(self, path, rep=None):
        self.path = path
        self.rep = rep or Reporter()
        self.data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 1 and isinstance(data.get("names"), dict):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            bak = self.path.with_name(self.path.name + ".bak")
            self.path.replace(bak)
            self.rep.log(f"deck_cache.json was unreadable; saved a copy as {bak.name}")
        return {"version": 1, "names": {}}

    def bucket(self, target, src_lang):
        return self.data["names"].setdefault(target, {}).setdefault(src_lang or "auto", {})

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
