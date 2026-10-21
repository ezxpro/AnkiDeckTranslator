from dataclasses import dataclass, field
import collections

from ankitool.core.constants import SEP
from ankitool.core.errors import AnkiToolError
from ankitool.core.progress import Reporter
from ankitool.core.text import lang_base


@dataclass
class RenameConfig:
    mode: str = None                 # None = ask | none | main | all
    method: str = None               # auto | manual | review | keep
    names: dict = field(default_factory=dict)      # CURRENT -> NEW

class DeckRenamer:
    """Translates/changes deck names. Translates loose segments (never the full path).
    Top-level decks are the first-level ones; sub-decks are the ones inside another deck (separated
    by '::'). Without questions: the menus are in DeckRenameCli."""

    def __init__(self, anki, client, target, cache, rep=None):
        self.anki, self.client, self.cache = anki, client, cache
        self.target, self.tbase = target, lang_base(target)
        self.rep = rep or Reporter()

    # ---- selection ----
    @staticmethod
    def paths(decks):
        return sorted({tuple(n.split(SEP)) for _, n in decks},
                      key=lambda p: tuple(s.lower() for s in p))

    @staticmethod
    def scope_for(mode, paths):
        """'main' -> first level only; 'all' -> all levels."""
        if mode == "main":
            return {p[:1] for p in paths}
        return {p[:i + 1] for p in paths for i in range(len(p))}

    # ---- names ----
    @staticmethod
    def clean_seg(s, fallback):
        s = s.replace(SEP, ":").strip()
        return s or fallback

    def auto_names(self, segments, src_lang):
        bucket = self.cache.bucket(self.target, src_lang)
        pending = sorted(s for s in segments if s not in bucket)
        self.rep.log(f"deck names: {len(pending)} to translate "
                     f"({len(segments) - len(pending)} already cached)")
        step = self.client.batch_size
        for i in range(0, len(pending), step):
            self.rep.check_cancelled()
            chunk = pending[i:i + step]
            res = self.client.translate(chunk, target_lang=self.target, source_lang=src_lang)
            if res is None:
                self.rep.warn(f"{self.client.label} is unavailable; remaining names are left as they are")
                break
            for s, r in zip(chunk, res):
                already = lang_base(r.detected_source_lang) == self.tbase  # already in the output language
                bucket[s] = s if already else self.clean_seg(r.text, s)
            self.cache.save()
        return {s: bucket.get(s, s) for s in segments}

    def auto_newseg(self, selected, overrides, src_lang, translate=True):
        """{path: new last segment}. 'overrides' (--deck-name) always wins;
        translate=False only applies the overrides."""
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
        """{id: (current name, new name)} for the decks that change only."""
        plan = {did: (old, self.final_name(old, newseg)) for did, old in decks}
        return {did: v for did, v in plan.items() if v[0] != v[1]}

    @staticmethod
    def duplicates(decks, plan):
        """Final names that repeat (Anki ignores case)."""
        finals = collections.defaultdict(list)
        for did, old in decks:
            finals[(plan[did][1] if did in plan else old).lower()].append(did)
        return [k for k, v in finals.items() if len(v) > 1]

    # ---- applying ----
    def apply(self, plan):
        decks_api = self.anki.col.decks
        done = 0
        for did, (old, new) in sorted(plan.items(), key=lambda kv: kv[1][0].count(SEP)):
            if decks_api.name(did) == new:               # the parent deck already renamed the sub-deck
                done += 1
                continue
            try:
                decks_api.rename(did, new)               # top-level before sub-decks
                done += 1
            except Exception as e:
                self.rep.warn(f" could not rename '{old}': {e}")
        self.rep.log(f"decks renamed: {done}/{len(plan)}")

    def run_auto(self, mode="all", overrides=None, src_lang=None, translate=True):
        """Library use: no questions. Returns the applied plan."""
        decks = self.anki.deck_list()
        selected = self.scope_for(mode, self.paths(decks))
        plan = self.make_plan(decks, self.auto_newseg(selected, overrides or {}, src_lang, translate))
        if self.duplicates(decks, plan):
            raise AnkiToolError("More than one deck would end up with the same name.")
        self.apply(plan)
        return plan
