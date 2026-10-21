from dataclasses import dataclass, field
import collections

from ankitool.core.constants import SEP
from ankitool.core.errors import AnkiToolError
from ankitool.core.progress import Reporter
from ankitool.core.text import lang_base


@dataclass
class RenameConfig:
    mode: str = None                 # None = perguntar | none | main | all
    method: str = None               # auto | manual | review | keep
    names: dict = field(default_factory=dict)      # ATUAL -> NOVO

class DeckRenamer:
    """Traduz/altera nomes de baralhos. Traduz segmentos soltos (nunca o caminho completo).
    Baralhos principais são os de primeiro nível; baralhos secundários são os que
    ficam dentro de outro baralho (separados por '::'). Sem perguntas: as menus ficam em
    DeckRenameCli."""

    def __init__(self, anki, client, target, cache, rep=None):
        self.anki, self.client, self.cache = anki, client, cache
        self.target, self.tbase = target, lang_base(target)
        self.rep = rep or Reporter()

    # ---- seleção ----
    @staticmethod
    def paths(decks):
        return sorted({tuple(n.split(SEP)) for _, n in decks},
                      key=lambda p: tuple(s.lower() for s in p))

    @staticmethod
    def scope_for(mode, paths):
        """'main' -> só o 1º nível; 'all' -> todos os níveis."""
        if mode == "main":
            return {p[:1] for p in paths}
        return {p[:i + 1] for p in paths for i in range(len(p))}

    # ---- nomes ----
    @staticmethod
    def clean_seg(s, fallback):
        s = s.replace(SEP, ":").strip()
        return s or fallback

    def auto_names(self, segments, src_lang):
        bucket = self.cache.bucket(self.target, src_lang)
        pending = sorted(s for s in segments if s not in bucket)
        self.rep.log(f"nomes de baralhos: {len(pending)} a traduzir "
                     f"({len(segments) - len(pending)} já no cache)")
        step = self.client.batch_size
        for i in range(0, len(pending), step):
            self.rep.check_cancelled()
            chunk = pending[i:i + step]
            res = self.client.translate(chunk, target_lang=self.target, source_lang=src_lang)
            if res is None:
                self.rep.warn(f"{self.client.label} fora do ar; nomes restantes ficam como estão")
                break
            for s, r in zip(chunk, res):
                already = lang_base(r.detected_source_lang) == self.tbase  # já no idioma de saída
                bucket[s] = s if already else self.clean_seg(r.text, s)
            self.cache.save()
        return {s: bucket.get(s, s) for s in segments}

    def auto_newseg(self, selected, overrides, src_lang, translate=True):
        """{caminho: novo último segmento}. 'overrides' (--deck-name) sempre vence;
        translate=False só aplica os overrides."""
        auto = {}
        if translate:
            auto = self.auto_names({p[-1] for p in selected if p[-1] not in overrides}, src_lang)
        newseg = {}
        for p in sorted(selected, key=lambda p: tuple(s.lower() for s in p)):
            seg = p[-1]
            new = overrides[seg] if seg in overrides else auto.get(seg, seg)
            if new != seg:
                newseg[p] = new
        return newseg

    @staticmethod
    def final_name(name, newseg):
        p = tuple(name.split(SEP))
        return SEP.join(newseg.get(p[:i + 1], p[i]) for i in range(len(p)))

    def make_plan(self, decks, newseg):
        """{id: (nome atual, nome novo)} só dos baralhos que mudam."""
        plan = {did: (old, self.final_name(old, newseg)) for did, old in decks}
        return {did: v for did, v in plan.items() if v[0] != v[1]}

    @staticmethod
    def duplicates(decks, plan):
        """Nomes finais repetidos (o Anki ignora maiúsculas/minúsculas)."""
        finals = collections.defaultdict(list)
        for did, old in decks:
            finals[(plan[did][1] if did in plan else old).lower()].append(did)
        return [k for k, v in finals.items() if len(v) > 1]

    # ---- aplicação ----
    def apply(self, plan):
        decks_api = self.anki.col.decks
        done = 0
        for did, (old, new) in sorted(plan.items(), key=lambda kv: kv[1][0].count(SEP)):
            if decks_api.name(did) == new:               # o baralho principal já arrastou o secundário
                done += 1
                continue
            try:
                decks_api.rename(did, new)               # principais antes dos secundários
                done += 1
            except Exception as e:
                self.rep.warn(f" não consegui renomear '{old}': {e}")
        self.rep.log(f"baralhos renomeados: {done}/{len(plan)}")

    def run_auto(self, mode="all", overrides=None, src_lang=None, translate=True):
        """Uso como biblioteca: sem perguntas. Devolve o plano aplicado."""
        decks = self.anki.deck_list()
        selected = self.scope_for(mode, self.paths(decks))
        plan = self.make_plan(decks, self.auto_newseg(selected, overrides or {}, src_lang, translate))
        if self.duplicates(decks, plan):
            raise AnkiToolError("Mais de um baralho ficaria com o mesmo nome.")
        self.apply(plan)
        return plan
