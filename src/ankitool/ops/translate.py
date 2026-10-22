from dataclasses import dataclass, field
import collections
import random
import re

from ankitool.anki.collection import AnkiCollection
from ankitool.core.constants import MIN_RATIO, SAMPLE
from ankitool.core.errors import ConfigError, ProviderUnavailable
from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools, h, lang_base
from concurrent.futures import ThreadPoolExecutor, as_completed


@dataclass
class TranslateConfig:
    target: str                                          # already validated (client.resolve_target)
    sample: int = SAMPLE
    min_ratio: float = MIN_RATIO
    only_fields: list = field(default_factory=list)      # [(note type|None, field)]
    skip_fields: list = field(default_factory=list)
    keep_scheduling: bool = False
    legacy: bool = True

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

    def detect(self, field_values):
        lang_cache = self.cache.lang
        detected = {}                                    # (mid, field) -> (language, ratio, example)
        sent = reused = 0
        for key, vs in field_values.items():
            self.rep.check_cancelled()
            uniq = list(dict.fromkeys(TextTools.plain(v) for v in vs))
            known = [p for p in uniq if h(p) in lang_cache]
            unknown = [p for p in uniq if h(p) not in lang_cache]
            pick = random.sample(known, min(self.sample, len(known)))
            fresh = random.sample(unknown, min(max(self.sample - len(pick), 0), len(unknown)))
            if fresh:
                if sent == 0:
                    self.rep.log(f"detecting field languages (sample of up to {self.sample} texts each)...")
                langs = self.client.detect(fresh, self.target)
                if langs is None:
                    raise ProviderUnavailable(f"{self.client.label} is unavailable; run again "
                                              f"(the cache keeps your progress).")
                for p, lang in zip(fresh, langs):
                    lang_cache[h(p)] = lang
                sent += len(fresh)
            reused += len(pick)
            sample = pick + fresh
            if not sample:                                # empty field (all text filtered out)
                continue
            lang, cnt = collections.Counter(lang_cache[h(p)] for p in sample).most_common(1)[0]
            ex = re.sub(r"\s+", " ", sample[0])[:45]
            detected[key] = (lang, cnt / len(sample), ex)

        if sent:
            self.cache.save()
            self.rep.log(f"language detection: {sent} texts sent, {reused} reused from cache")
        else:
            self.rep.log("languages already detected (cache): no characters spent on detection")
        return detected

class FieldTranslator:
    """Translates the chosen fields (cache per output + source + text hash)."""

    def __init__(self, client, cache, rep):
        self.client, self.cache, self.rep = client, cache, rep

    def translate(self, chosen, field_values, dry_run=False):
        todo = collections.defaultdict(list)             # (source, output) -> texts
        queued, cached = set(), set()
        for (mid, f), (L, T) in chosen.items():
            for v in field_values[(mid, f)]:
                if self.cache.get(T, L, v) is not None:
                    cached.add((L, T, v))
                elif (L, T, v) not in queued:
                    queued.add((L, T, v))
                    todo[(L, T)].append(v)
        chars = sum(len(self.client.mask(v)) for (_, _, v) in queued)
        est = Estimate(len(queued), chars, len(cached), {k: len(v) for k, v in todo.items()})
        self.rep.log(f"to translate: {est.texts} texts (~{chars} characters); "
                     f"already in cache (no cost): {est.cached}")
        if dry_run:
            return est
        for (L, T), texts in todo.items():
            texts = sorted(texts)
            step = self.client.batch_size
            chunks = [texts[i:i + step] for i in range(0, len(texts), step)]
            done = 0
            pool = ThreadPoolExecutor(max_workers=self.client.max_workers)
            try:
                futures = {pool.submit(self.client.translate, c, target_lang=T,
                                       source_lang=L, protect_tags=True): c for c in chunks}
                for fut in as_completed(futures):       # cache/report only on the main thread
                    self.rep.check_cancelled()
                    chunk, res = futures[fut], fut.result()
                    if res is None:
                        raise ProviderUnavailable(f"{self.client.label} is unavailable; run again "
                                                  f"(the cache keeps your progress).")
                    for src, r in zip(chunk, res):
                        out = r.text
                        if TextTools.PROT.findall(out) == TextTools.PROT.findall(src):
                            self.cache.put(T, L, src, out)
                        else:
                            self.rep.warn(" damaged tag, keeping original:", TextTools.plain(src)[:50])
                    self.cache.save()
                    done += len(chunk)
                    self.rep.log(f" [{L}->{T}] {done}/{len(texts)}")
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
        return est

class TranslateOperation:
    """Translation flow for one deck, with no questions and no print/input:
    open -> detect -> plan -> translate -> apply -> export -> close."""

    def __init__(self, client, cache, cfg, rep=None):
        self.client, self.cache, self.cfg = client, cache, cfg
        self.rep = rep or Reporter()
        self.target, self.tbase = cfg.target, lang_base(cfg.target)
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
        return FieldTranslator(self.client, self.cache, self.rep).translate(
            chosen, self.anki.field_values, dry_run=dry_run)

    def apply(self, chosen):
        self.anki.apply_translations(chosen, self.cache)

    def export(self, out):
        self.anki.export(out)

    def close(self):
        if self.anki:
            self.anki.close()
