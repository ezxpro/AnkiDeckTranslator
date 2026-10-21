from dataclasses import dataclass, field
import collections
import random
import re

from ankitool.anki.collection import AnkiCollection
from ankitool.core.constants import MIN_RATIO, SAMPLE
from ankitool.core.errors import ConfigError, ProviderUnavailable
from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools, h, lang_base


@dataclass
class TranslateConfig:
    target: str                                          # já validado (client.resolve_target)
    sample: int = SAMPLE
    min_ratio: float = MIN_RATIO
    only_fields: list = field(default_factory=list)      # [(tipo_de_nota|None, campo)]
    skip_fields: list = field(default_factory=list)
    keep_scheduling: bool = False
    legacy: bool = True

@dataclass
class Estimate:
    texts: int
    chars: int
    cached: int
    by_pair: dict                                        # (origem, saída) -> nº de textos

class FieldLanguageDetector:
    """Detecta o idioma de cada campo por amostragem. Cache por texto (hash do texto simples):
    textos já detectados antes não são reenviados. A amostra prioriza textos conhecidos,
    então só textos realmente novos gastam caracteres."""

    def __init__(self, client, cache, target, rep, sample=SAMPLE):
        self.client, self.cache, self.target, self.sample = client, cache, target, sample
        self.rep = rep

    def detect(self, field_values):
        lang_cache = self.cache.lang
        detected = {}                                    # (mid, campo) -> (idioma, proporção, exemplo)
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
                    self.rep.log(f"detectando o idioma dos campos (amostra de até {self.sample} textos cada)...")
                langs = self.client.detect(fresh, self.target)
                if langs is None:
                    raise ProviderUnavailable(f"{self.client.label} fora do ar; rode de novo "
                                              f"(o cache preserva o progresso).")
                for p, lang in zip(fresh, langs):
                    lang_cache[h(p)] = lang
                sent += len(fresh)
            reused += len(pick)
            sample = pick + fresh
            lang, cnt = collections.Counter(lang_cache[h(p)] for p in sample).most_common(1)[0]
            ex = re.sub(r"\s+", " ", sample[0])[:45]
            detected[key] = (lang, cnt / len(sample), ex)

        if sent:
            self.cache.save()
            self.rep.log(f"detecção de idioma: {sent} textos enviados, {reused} reaproveitados do cache")
        else:
            self.rep.log("idiomas já detectados antes (cache): nenhum caractere gasto na detecção")
        return detected

class FieldTranslator:
    """Traduz os campos escolhidos (cache por saída + origem + hash do texto)."""

    def __init__(self, client, cache, rep):
        self.client, self.cache, self.rep = client, cache, rep

    def translate(self, chosen, field_values, dry_run=False):
        todo = collections.defaultdict(list)             # (origem, saída) -> textos
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
        self.rep.log(f"a traduzir: {est.texts} textos (~{chars} caracteres); "
                     f"já estavam no cache (sem custo): {est.cached}")
        if dry_run:
            return est

        for (L, T), texts in todo.items():
            texts = sorted(texts)
            step = self.client.batch_size
            for i in range(0, len(texts), step):
                self.rep.check_cancelled()
                chunk = texts[i:i + step]
                res = self.client.translate(chunk, target_lang=T, source_lang=L,
                                            protect_tags=True)
                if res is None:
                    raise ProviderUnavailable(f"{self.client.label} fora do ar; rode de novo "
                                              f"(o cache preserva o progresso).")
                for src, r in zip(chunk, res):
                    out = r.text
                    ok = TextTools.PROT.findall(out) == TextTools.PROT.findall(src)
                    if not ok:
                        self.rep.warn(" tag danificada, mantido original:", TextTools.plain(src)[:50])
                    self.cache.put(T, L, src, out if ok else src)
                self.cache.save()
                self.rep.log(f" [{L}->{T}] {min(i + step, len(texts))}/{len(texts)}")
        return est

class TranslateOperation:
    """Fluxo de tradução de um baralho, sem perguntas e sem print/input:
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
                self.rep.warn(f"--field '{(nt + ':') if nt else ''}{fld}' não corresponde a nenhum campo")
            self.rep.log(f"campos considerados: {len(self.anki.field_values)} de {before} "
                         f"(por --field / --skip-field)")
            if not self.anki.field_values:
                raise ConfigError("Nenhum campo sobrou depois de --field/--skip-field. "
                                  "Use o comando 'info' para ver os nomes dos campos.")

    def detect(self):
        return FieldLanguageDetector(self.client, self.cache, self.target, self.rep,
                                     sample=self.cfg.sample).detect(self.anki.field_values)

    def language_options(self, detected):
        """([(código, nº de campos)] que dá para traduzir, nº de campos já no idioma de saída)."""
        mr = self.cfg.min_ratio
        counts = collections.Counter(l for (l, r, _) in detected.values()
                                     if r >= mr and lang_base(l) != self.tbase)
        already = sum(1 for (l, r, _) in detected.values()
                      if r >= mr and lang_base(l) == self.tbase)
        return counts.most_common(), already

    @staticmethod
    def validate_languages(raw, valid):
        """'IT,EN' / 'all' / None -> conjunto de códigos; ConfigError se algum não existe."""
        raw = (raw or "all").upper()
        if raw == "ALL":
            return set(valid)
        picked = {t for t in re.split(r"[,\s;]+", raw) if t}
        bad = sorted(picked - set(valid))
        if bad:
            raise ConfigError(f"Idioma(s) não detectado(s) nos campos: {', '.join(bad)}. "
                              f"Detectados: {', '.join(valid)}")
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
