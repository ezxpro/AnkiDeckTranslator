import time

from dataclasses import dataclass, field
import collections
import random
import re

from ankitool.anki.collection import AnkiCollection
from ankitool.cache.translation import CacheView, DEFAULT_MODE
from ankitool.core.pool import run_chunks
from ankitool.core.constants import MIN_DETECT_CHARS, MIN_RATIO, SAMPLE
from ankitool.core.errors import ConfigError, ProviderUnavailable
from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools, lang_base


@dataclass
class TranslateConfig:
    target: str                                          # already validated (client.resolve_target)
    sample: int = SAMPLE
    min_ratio: float = MIN_RATIO
    only_fields: list = field(default_factory=list)      # [(note type|None, field)]
    skip_fields: list = field(default_factory=list)
    keep_scheduling: bool = False
    legacy: bool = True
    cache_mode: str = DEFAULT_MODE                       # shared | provider | isolated

@dataclass
class Estimate:
    texts: int
    chars: int
    cached: int
    by_pair: dict                                        # (source, output) -> number of texts

class FieldLanguageDetector:
    """Detects the language of each field by sampling. Cache per text (hash of the plain text):
    texts already detected before are not sent again. The sample prefers known texts,
    so only genuinely new texts spend characters."""

    def __init__(self, client, cache, target, rep, sample=SAMPLE):
        self.client, self.cache, self.target, self.sample = client, cache, target, sample
        self.rep = rep
        client.on_wait = rep.waiting

    def detect(self, field_values):
        lang_cache = self.cache.lang
        plan, to_send, seen = {}, [], set()
        for key, vs in field_values.items():           # 1) plan every field first
            self.rep.check_cancelled()
            uniq = list(dict.fromkeys(TextTools.plain(v) for v in vs if TextTools.has_letters(v)))
            long = [p for p in uniq if len(p) >= MIN_DETECT_CHARS]
            uniq = long or sorted(uniq, key=len, reverse=True)[:self.sample]   # only short texts: use the longest
            known = [p for p in uniq if p in lang_cache]
            unknown = [p for p in uniq if p not in lang_cache]
            pick = random.sample(known, min(self.sample, len(known)))
            fresh = random.sample(unknown, min(max(self.sample - len(pick), 0), len(unknown)))
            plan[key] = (pick, fresh)
            for p in fresh:
                if p not in seen:
                    seen.add(p)
                    to_send.append(p)
        sent, reused = 0, sum(len(pick) for pick, _ in plan.values())
        if to_send:
            self.rep.log(f"detecting languages of {len(to_send)} new texts...")
        step = self.client.batch_size
        chunks = [to_send[i:i + step] for i in range(0, len(to_send), step)]

        def on_result(chunk, langs):
            nonlocal sent
            if langs is None:
                raise ProviderUnavailable(f"{self.client.label} is unavailable; run again "
                                          f"(the cache keeps your progress).")
            for p, lang in zip(chunk, langs):
                self.cache.set_lang(p, lang, self.client.name)
            sent += len(chunk)

        run_chunks(lambda c: self.client.detect(c, self.target), chunks,
                   self.client.max_workers, self.rep, on_result)
        detected = {}
        for key, (pick, fresh) in plan.items():         # 3) majority language per field
            sample = pick + fresh
            if not sample:                              # empty field (all text filtered out)
                continue
            votes = collections.Counter()
            for p in sample:                            # longer texts weigh more
                votes[lang_cache[p]] += len(p)
            lang, weight = votes.most_common(1)[0]
            ex = re.sub(r"\s+", " ", max(sample, key=len))[:45]
            detected[key] = (lang, weight / sum(votes.values()), ex)

        if sent:
            self.cache.save()
            self.rep.log(f"language detection: {sent} texts sent, {reused} reused from cache")
        else:
            self.rep.log("languages already detected (cache): no characters spent on detection")
        return detected

    
class FieldTranslator:
    """Translates the chosen fields, reusing and saving translations through a CacheView."""

    def __init__(self, client, view, rep):
        self.client, self.view, self.rep = client, view, rep
        client.on_wait = rep.waiting

    def translate(self, chosen, field_values, dry_run=False):
        todo = collections.defaultdict(list)             # (source, output) -> texts
        queued, cached = set(), set()
        for (mid, f), (L, T) in chosen.items():
            for v in field_values[(mid, f)]:
                if not TextTools.has_letters(v) or (L, T, v) in cached or (L, T, v) in queued:
                    continue
                if self.view.read(T, L, v) is not None:
                    cached.add((L, T, v))
                else:
                    queued.add((L, T, v))
                    todo[(L, T)].append(v)
        chars = sum(len(self.client.mask(v)) for (_, _, v) in queued)
        est = Estimate(len(queued), chars, len(cached), {k: len(v) for k, v in todo.items()})
        self.rep.log(f"to translate: {est.texts} texts (~{chars} characters); "
                     f"already in cache (no cost): {est.cached}")
        if dry_run:
            return est
        last_save = time.monotonic()
        for (L, T), texts in todo.items():
            texts = sorted(texts)
            step = self.client.batch_size
            chunks = [texts[i:i + step] for i in range(0, len(texts), step)]
            done = 0

            def on_result(chunk, res):
                nonlocal done, last_save
                if res is None:
                    raise ProviderUnavailable(f"{self.client.label} is unavailable; run again "
                                              f"(the cache keeps your progress).")
                for src, r in zip(chunk, res):
                    out = r.text
                    if TextTools.PROT.findall(out) == TextTools.PROT.findall(src):
                        self.view.write(T, L, src, out)
                    else:
                        self.rep.warn(" damaged tag, keeping original:", TextTools.plain(src)[:50])
                done += len(chunk)
                if time.monotonic() - last_save >= 5:   # throttled: rewriting the file each chunk is O(n²)
                    self.view.save()
                    last_save = time.monotonic()
                self.rep.log(f" [{L}->{T}] {done}/{len(texts)}")

            try:
                run_chunks(lambda c: self.client.translate(c, target_lang=T, source_lang=L,
                                                           protect_tags=True),
                           chunks, self.client.max_workers, self.rep, on_result)
            finally:
                self.view.save()                        # keep everything translated so far, even on error/cancel                      # keep everything translated so far, even on error/cancel
        return est

class TranslateOperation:
    """Translation flow for one deck, with no questions and no print/input:
    open -> detect -> plan -> translate -> apply -> export -> close."""

    def __init__(self, client, cache, cfg, rep=None):
        self.client, self.cache, self.cfg = client, cache, cfg
        self.rep = rep or Reporter()
        self.target, self.tbase = cfg.target, lang_base(cfg.target)
        self.view = CacheView(cache, client.name, client.variant, cfg.cache_mode)
        self.anki = None

    def open(self, src):
        cfg = self.cfg
        self.anki = AnkiCollection(src, cfg.keep_scheduling, cfg.legacy, self.rep)
        self.anki.import_package()
        self.anki.load_notes()
        if cfg.only_fields or cfg.skip_fields:
            before = len(self.anki.field_values)
            for nt, fld in self.anki.filter_fields(cfg.only_fields, cfg.skip_fields):
                self.rep.warn(f"--field '{(nt + ':') if nt else ''}{fld}' does not match any field")
            self.rep.log(f"fields considered: {len(self.anki.field_values)} of {before} "
                         f"(by --field / --skip-field)")
            if not self.anki.field_values:
                raise ConfigError("No fields left after --field/--skip-field. "
                                  "Use the 'info' command to see the field names.")

    def detect(self):
        return FieldLanguageDetector(self.client, self.cache, self.target, self.rep,
                                     sample=self.cfg.sample).detect(self.anki.field_values)

    def language_options(self, detected):
        """([(code, number of fields)] that can be translated, number of fields already in the output language)."""
        mr = self.cfg.min_ratio
        counts = collections.Counter(l for (l, r, _) in detected.values()
                                     if r >= mr and lang_base(l) != self.tbase)
        already = sum(1 for (l, r, _) in detected.values()
                      if r >= mr and lang_base(l) == self.tbase)
        return counts.most_common(), already

    @staticmethod
    def validate_languages(raw, valid):
        """'IT,EN' / 'all' / None -> set of codes; ConfigError if any doesn't exist."""
        raw = (raw or "all").upper()
        if raw == "ALL":
            return set(valid)
        picked = {t for t in re.split(r"[,\s;]+", raw) if t}
        bad = sorted(picked - set(valid))
        if bad:
            raise ConfigError(f"Language(s) not detected in the fields: {', '.join(bad)}. "
                              f"Detected: {', '.join(valid)}")
        return picked

    def plan(self, detected, picked):
        return {k: (lang, self.target) for k, (lang, ratio, _) in detected.items()
                if lang in picked and ratio >= self.cfg.min_ratio}

    def translate(self, chosen, dry_run=False):
        return FieldTranslator(self.client, self.view, self.rep).translate(
            chosen, self.anki.field_values, dry_run=dry_run)

    def apply(self, chosen):
        self.anki.apply_translations(chosen, self.view)

    def export(self, out):
        self.anki.export(out)

    def close(self):
        if self.anki:
            self.anki.close()