import json

from ankitool.core.progress import Reporter
from ankitool.core.text import h


class TranslationCache:
    """cache.json (v3)
    trans: {SAIDA: {ORIGEM: {hash(texto): tradução}}}   ("?" = origem desconhecida, vinda da v2)
    lang:  {hash(texto simples): idioma detectado}
    Só hashes vão para o arquivo: o texto original do baralho não é guardado."""

    VERSION = 3

    def __init__(self, path, valid_target_codes, rep=None):
        self.path = path
        self.valid_target_codes = valid_target_codes
        self.rep = rep or Reporter()                     # antes de _load(), que usa self.rep
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
                self.path.replace(bak)
                self.rep.log(f"cache.json estava ilegível; guardei uma cópia em {bak.name} e comecei um novo")
                data = {}
        if not isinstance(data, dict):
            data = {}
        ver = data.get("version")
        if data and ver not in (2, 3):          # formato antigo: chaves "SAIDA|ORIGEM|texto"
            old, data, migrated = data, {}, 0
            data["trans"] = {}
            for k, v in old.items():
                parts = k.split("|", 2)
                if len(parts) == 3 and parts[0] in self.valid_target_codes:
                    data["trans"].setdefault(parts[0], {})[parts[2]] = v
                    migrated += 1
            self.rep.log(f"cache antigo convertido: {migrated} traduções aproveitadas")
            ver = 2
        if ver == 2:                            # v2: texto original como chave, origem desconhecida
            trans, migrated = {}, 0
            for t_code, d in data.get("trans", {}).items():
                trans[t_code] = {"?": {h(t): out for t, out in d.items()}}
                migrated += len(d)
            data = {"trans": trans}
            self.rep.log(f"cache v2 convertido para hashes: {migrated} traduções aproveitadas "
                         f"(origem desconhecida, ficam em '?')")
        return {"trans": data.get("trans", {}), "lang": data.get("lang", {})}

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")     # grava num temporário e troca:
        payload = {"version": self.VERSION, "trans": self.trans, "lang": self.lang}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(self.path)                                 # fechar o programa não corrompe

    def get(self, T, L, v):
        """Tradução em cache de v (saída T, origem L), ou None. O bucket '?' (cache antigo)
        só é consultado se o bucket exato não tiver o texto; nada novo é gravado nele."""
        tt, k = self.trans.get(T, {}), h(v)
        r = tt.get(L, {}).get(k)
        return r if r is not None else tt.get("?", {}).get(k)

    def put(self, T, L, src, out):
        self.trans.setdefault(T, {}).setdefault(L, {})[h(src)] = out

    def count(self):
        return sum(len(d) for t in self.trans.values() for d in t.values())
