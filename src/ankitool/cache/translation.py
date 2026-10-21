import json

from ankitool.core.progress import Reporter
from ankitool.core.text import h


class TranslationCache:
    """cache.json (v3)
    trans: {OUTPUT: {SOURCE: {hash(text): translation}}}   ("?" = unknown source, from v2)
    lang:  {hash(plain text): detected language}
    Only hashes go to the file: the original deck text is not stored."""

    VERSION = 3

    def __init__(self, path, valid_target_codes, rep=None):
        self.path = path
        self.valid_target_codes = valid_target_codes
        self.rep = rep or Reporter()                     # before _load(), which uses self.rep
        data = self._load()
        self.trans = data["trans"]
        self.lang = data["lang"]

    def _load(self):
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bak = self.path.with_name(self.path.name + ".bak")
                # Rotate so a second corruption does not overwrite the previous backup.
                i = 1
                while bak.exists():
                    bak = self.path.with_name(f"{self.path.name}.bak{i}")
                    i += 1
                self.path.replace(bak)
                self.rep.log(f"cache.json was unreadable; saved a copy as {bak.name} and started a new one")
                data = {}
        if not isinstance(data, dict):
            data = {}
        ver = data.get("version")
        if data and ver not in (2, 3):          # old format: keys "OUTPUT|SOURCE|text"
            old, data, migrated = data, {}, 0
            data["trans"] = {}
            for k, v in old.items():
                parts = k.split("|", 2)
                if len(parts) == 3 and parts[0] in self.valid_target_codes:
                    data["trans"].setdefault(parts[0], {})[parts[2]] = v
                    migrated += 1
            data["lang"] = old.get("lang", {}) if isinstance(old.get("lang"), dict) else {}
            self.rep.log(f"old cache converted: {migrated} translations kept")
            ver = 2
        if ver == 2:                            # v2: original text as key, unknown source
            trans, migrated = {}, 0
            for t_code, d in data.get("trans", {}).items():
                trans[t_code] = {"?": {h(t): out for t, out in d.items()}}
                migrated += len(d)
            data = {"trans": trans}
            self.rep.log(f"v2 cache converted to hashes: {migrated} translations kept "
                         f"(unknown source, stored under '?')")
        return {"trans": data.get("trans", {}), "lang": data.get("lang", {})}

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")     # write to a temp file and swap:
        payload = {"version": self.VERSION, "trans": self.trans, "lang": self.lang}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(self.path)                                 # closing the program can't corrupt it

    def get(self, T, L, v):
        """Cached translation of v (output T, source L), or None. The '?' bucket (old cache)
        is only checked if the exact bucket doesn't have the text; nothing new is written to it."""
        tt, k = self.trans.get(T, {}), h(v)
        r = tt.get(L, {}).get(k)
        return r if r is not None else tt.get("?", {}).get(k)

    def put(self, T, L, src, out):
        self.trans.setdefault(T, {}).setdefault(L, {})[h(src)] = out

    def count(self):
        return sum(len(d) for t in self.trans.values() for d in t.values())
