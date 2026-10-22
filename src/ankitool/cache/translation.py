"""One translation cache for every service: cache.json (version 4).

  trans: {TARGET: {SOURCE: {hash(text): {SERVICE: translation}}}}
  lang:  {hash(plain text): detected language}           shared by all services
  decks: {TARGET: {SOURCE: {deck name: {SERVICE: name}}}}

Only hashes of the texts are stored for translations (the deck text is not written).
Each service writes only its own entry, so services never overwrite each other.

Modes (per service, see CacheView):
  shared   (default): the service's own translation if it has one; otherwise a translation
                      stored by another service (the first service in alphabetical order,
                      so the result is always the same).
  isolated: only the service's own translation; others are ignored.
The default mode is 'shared'; a service can have its own mode in config.json (providers.NAME.cache)."""
import json
from pathlib import Path

from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools, h

MODES = ("shared", "isolated")
DEFAULT_MODE = "shared"
VERSION = 4


def _pick(entry, service, mode):
    if not entry:
        return None
    if service in entry:
        return entry[service]
    if mode == "isolated":
        return None
    return entry[min(entry)]                       # shared: deterministic choice among the others


class TranslationCache:
    def __init__(self, path, rep=None):
        self.path = Path(path)
        self.rep = rep or Reporter()
        self.trans, self.lang, self.decks = {}, {}, {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or data.get("version") != VERSION:
            bak = self.path.with_name(self.path.name + ".bak")     # never overwrite an old copy
            i = 1
            while bak.exists():
                bak = self.path.with_name(f"{self.path.name}.bak{i}")
                i += 1
            self.path.replace(bak)
            self.rep.log(f"{self.path.name} was not a version {VERSION} cache; saved it as {bak.name}")
            return
        self.trans = data.get("trans", {})
        self.lang = data.get("lang", {})
        self.decks = data.get("decks", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")     # write a temp file and swap it in
        payload = {"version": VERSION, "trans": self.trans, "lang": self.lang, "decks": self.decks}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, T, L, text, service, mode=DEFAULT_MODE):
        return _pick(self.trans.get(T, {}).get(L, {}).get(h(text)), service, mode)

    def put(self, T, L, src, out, service):
        self.trans.setdefault(T, {}).setdefault(L, {}).setdefault(h(src), {})[service] = out

    def deck_get(self, T, L, seg, service, mode=DEFAULT_MODE):
        return _pick(self.decks.get(T, {}).get(L or "auto", {}).get(seg), service, mode)

    def deck_put(self, T, L, seg, out, service):
        self.decks.setdefault(T, {}).setdefault(L or "auto", {}).setdefault(seg, {})[service] = out

    def invalidate(self, service=None, target=None, lang=None, text=None):
        """Deletes the translations and deck names matching ALL the given filters.
        Returns (translations removed, deck names removed). Text languages are kept."""
        key = h(text) if text is not None else None
        n_tr = n_dk = 0
        for T in list(self.trans):
            if target and T != target:
                continue
            for L in list(self.trans[T]):
                if lang and L != lang:
                    continue
                for k in list(self.trans[T][L]):
                    if key is not None and k != key:
                        continue
                    entry = self.trans[T][L][k]
                    for s in [s for s in entry if service is None or s == service]:
                        del entry[s]
                        n_tr += 1
                    if not entry:
                        del self.trans[T][L][k]
        for T in list(self.decks):
            if target and T != target:
                continue
            for L in list(self.decks[T]):
                if lang and L != lang:
                    continue
                for seg in list(self.decks[T][L]):
                    if text is not None and seg != text:
                        continue
                    entry = self.decks[T][L][seg]
                    for s in [s for s in entry if service is None or s == service]:
                        del entry[s]
                        n_dk += 1
                    if not entry:
                        del self.decks[T][L][seg]
        self._prune()
        return n_tr, n_dk

    def clear_all(self):
        """Empties everything. Returns (translations, text languages, deck names) removed."""
        n = (self.count(), len(self.lang), sum(len(s) for b in self.decks.values() for s in b.values()))
        self.trans, self.lang, self.decks = {}, {}, {}
        return n

    def _prune(self):
        for root in (self.trans, self.decks):
            for T in [t for t in root if not any(root[t].values())]:
                del root[T]
            for T in root:
                for L in [L for L in root[T] if not root[T][L]]:
                    del root[T][L]

    def count(self):
        return sum(len(e) for by_src in self.trans.values() for by_h in by_src.values() for e in by_h.values())

    def stats(self):
        """{service: {target: number of translations}}"""
        out = {}
        for T, by_src in self.trans.items():
            for by_h in by_src.values():
                for entry in by_h.values():
                    for s in entry:
                        out.setdefault(s, {})[T] = out.get(s, {}).get(T, 0) + 1
        return out


class CacheView:
    """What one service may read and write: its name and its mode."""

    def __init__(self, cache, service, mode=DEFAULT_MODE):
        if mode not in MODES:
            raise ValueError(f"unknown cache mode: {mode}")
        self.cache, self.service, self.mode = cache, service, mode

    @property
    def lang(self):
        return self.cache.lang

    def read(self, T, L, text):
        if not TextTools.has_letters(text):          # numbers/symbols are never translated
            return None
        return self.cache.get(T, L, text, self.service, self.mode)

    get = read                                     # used by AnkiCollection.apply_translations

    def write(self, T, L, src, out):
        self.cache.put(T, L, src, out, self.service)

    def read_deck(self, T, L, seg):
        return self.cache.deck_get(T, L, seg, self.service, self.mode)

    def write_deck(self, T, L, seg, out):
        self.cache.deck_put(T, L, seg, out, self.service)

    def save(self):
        self.cache.save()
