# pip install anki   (+ deepl, se for usar o serviço DeepL)

import argparse, json, os, re, sys, time, random, tempfile, collections, hashlib, threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ImportAnkiPackageOptions, ExportAnkiPackageOptions)

SCRIPT_DIR = Path(__file__).resolve().parent
SAMPLE, MIN_RATIO = 10, 0.6
DEFAULT_PROVIDER = "deepl"
SEP = "::"


# ======================================================================
# Utilitários de console
# ======================================================================
class Ui:
    """Entrada/saída de console: log, perguntas, menus e barra de progresso.
    Com Ui.auto = True (opção --yes), nenhuma pergunta é feita: vale o valor padrão."""

    auto = False

    @staticmethod
    def log(*a):
        print(time.strftime("%H:%M:%S"), *a, flush=True)

    @staticmethod
    def ask(msg, default):
        if Ui.auto:
            return default
        return input(f"{msg} [{default}]: ").strip().strip('"') or default

    @staticmethod
    def yes(msg, default="n"):
        return Ui.ask(f"{msg} (s/n)", default).lower() in ("s", "sim", "y", "yes")

    @staticmethod
    def choose(title, opts, default):
        print(f"\n{title}")
        for k, v in opts.items():
            print(f" {k}) {v}")
        while True:
            r = Ui.ask("Escolha", default)
            if r in opts:
                return r
            print(" Opção inválida.")

    @staticmethod
    def show_langs(title, langs):
        items = sorted(langs, key=lambda l: l.name)
        half = (len(items) + 1) // 2
        print(f"\n{title}")
        for i in range(half):
            left = f" {items[i].code:<8}{items[i].name}"
            right = ""
            if i + half < len(items):
                r = items[i + half]
                right = f"{r.code:<8}{r.name}"
            print(f"{left:<40}{right}")
        print()

    @staticmethod
    def progress(items, label, total=None):
        """Percorre 'items' mostrando andamento. Em terminal atualiza a mesma linha;
        com saída redirecionada (log/arquivo) imprime a cada 10%."""
        total = len(items) if total is None else total
        tty = sys.stdout.isatty()
        last, nxt = 0.0, 0.1
        for i, x in enumerate(items, 1):
            yield x
            if tty:
                now = time.time()
                if now - last >= 0.2 or i == total:
                    print(f"\r{time.strftime('%H:%M:%S')} {label}: {i}/{total} ({i * 100 // total}%)",
                          end="", flush=True)
                    last = now
            elif i / total >= nxt or i == total:
                Ui.log(f"{label}: {i}/{total} ({i * 100 // total}%)")
                while nxt <= i / total:
                    nxt += 0.1
        if tty and total:
            print()


log = Ui.log


class Heartbeat:
    """Para chamadas longas sem progresso interno (importar/exportar):
    mostra 'ainda trabalhando... Ns' a cada poucos segundos."""

    def __init__(self, msg, every=5):
        self.msg, self.every = msg, every

    def __enter__(self):
        self.t0, self.stop = time.time(), threading.Event()
        log(self.msg + "...")
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while not self.stop.wait(self.every):
            log(f"  {self.msg}... {int(time.time() - self.t0)}s")

    def __exit__(self, exc_type, *rest):
        self.stop.set()
        if exc_type is None:
            log(f"{self.msg}: concluído em {time.time() - self.t0:.1f}s")
        return False


# ======================================================================
# Texto: hash e proteção de tags
# ======================================================================
class TextTools:
    PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
    WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)

    @staticmethod
    def h(s):
        """Hash curto e estável de um texto: o cache guarda isso no lugar do texto original."""
        return hashlib.blake2b(s.encode("utf-8"), digest_size=12).hexdigest()

    @classmethod
    def mask(cls, t):
        return cls.PROT.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', t)

    @classmethod
    def unmask(cls, t):
        return cls.WRAP.sub(r"\1", t)

    @staticmethod
    def plain(s):
        return re.sub(r"\[sound:[^\]]+\]|<[^>]+>|&\w+;|\{\{c\d+::|\}\}", "", s).strip()


h = TextTools.h


# ======================================================================
# Provedores de tradução
# ======================================================================
@dataclass(frozen=True)
class Language:
    code: str                                  # código na grafia do próprio provedor
    name: str


@dataclass(frozen=True)
class TranslationResult:
    text: str
    detected_source_lang: str = None           # idioma detectado (grafia do provedor)


def lang_base(code):
    """'pt-BR' / 'PT_br' / 'PT' -> 'PT': compara idiomas ignorando região e maiúsculas."""
    return re.split(r"[-_]", (code or "").upper())[0]


def load_secret(env_name, label):
    """Procura a chave na variável de ambiente <env_name>; se não achar, lê o arquivo .env
    que fica na mesma pasta do script (linha no formato NOME=valor)."""
    key = os.environ.get(env_name, "").strip()
    if not key:
        env_file = SCRIPT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.replace("export", "").strip() == env_name:
                    key = value.strip().strip('"').strip("'")
                    break
    if not key:
        raise SystemExit(f"Chave do {label} não encontrada. Defina a variável de ambiente "
                         f"{env_name} ou crie um arquivo .env ao lado do script com:\n"
                         f"{env_name}=sua-chave-aqui")
    return key


PROVIDERS = {}                                 # nome -> classe; preenchido por @register_provider


def register_provider(cls):
    PROVIDERS[cls.name] = cls
    return cls


def make_provider(name):
    if name not in PROVIDERS:
        raise SystemExit(f"Serviço de tradução desconhecido: {name}. "
                         f"Disponíveis: {', '.join(sorted(PROVIDERS))}")
    return PROVIDERS[name]()


class TranslationProvider(ABC):
    """Contrato de um serviço de tradução. O restante do programa só conhece esta interface.

    Para adicionar outro serviço (Google, Azure, LibreTranslate, ...):
      1. crie uma subclasse com name, label e key_env;
      2. implemente connect, fetch_target_languages, fetch_source_languages e _translate
         (e, se houver, usage e detect);
      3. decore a classe com @register_provider.
    Ele passa a valer em --provider e no menu interativo, com cache próprio
    (cache_<name>.json). Se o serviço não entende HTML com translate="no", sobrescreva
    mask/unmask para proteger [sound:...] e {{c1::...}} de outra forma."""

    name = ""                                  # identificador usado em --provider
    label = ""                                 # nome para exibição
    key_env = ""                               # variável de ambiente / .env com a chave
    default_target = "PT-BR"                   # código sugerido como idioma de saída
    batch_size = 40                            # textos por chamada
    retries = 3
    transient_errors = (ConnectionError, TimeoutError)   # erros que valem nova tentativa
    cache_tag = None                           # None -> "_<name>" nos nomes dos arquivos de cache

    def __init__(self, key=None):
        self.key = key or load_secret(self.key_env, self.label)
        self.connect(self.key)
        self.target_langs = self.fetch_target_languages()
        self.source_langs = self.fetch_source_languages()
        self.target_codes = {l.code for l in self.target_langs}
        self.source_names = {l.code: l.name for l in self.source_langs}
        self._target_by_norm = {l.code.upper(): l.code for l in self.target_langs}

    # ---- a implementar em cada serviço ----
    @abstractmethod
    def connect(self, key):
        """Cria o cliente do serviço (e ajusta transient_errors, se preciso)."""

    @abstractmethod
    def fetch_target_languages(self):
        """-> lista de Language disponíveis como saída."""

    @abstractmethod
    def fetch_source_languages(self):
        """-> lista de Language reconhecidos como origem."""

    @abstractmethod
    def _translate(self, texts, target_lang, source_lang, html):
        """Uma chamada ao serviço. Devolve uma TranslationResult por texto, na mesma ordem.
        html=True: os textos contêm tags <span translate="no"> que não podem ser traduzidas."""

    def usage(self):
        """Consumo atual (para exibir), ou None se o serviço não informa."""
        return None

    def detect(self, texts, target_lang):
        """Idioma de cada texto (None se o serviço estiver fora do ar). Por padrão traduz e
        lê o idioma detectado; sobrescreva se o serviço tiver um endpoint de detecção."""
        res = self.translate(texts, target_lang)
        return None if res is None else [r.detected_source_lang for r in res]

    # ---- comum a todos ----
    def mask(self, t):
        return TextTools.mask(t)

    def unmask(self, t):
        return TextTools.unmask(t)

    def translate(self, texts, target_lang, source_lang=None, protect_tags=False):
        """Traduz com retentativa; devolve None se o serviço não responder.
        protect_tags=True protege [sound:...] e {{c1::...}} (restaurados no resultado)."""
        payload = [self.mask(t) for t in texts] if protect_tags else list(texts)
        for _ in range(self.retries):
            try:
                res = self._translate(payload, target_lang, source_lang, protect_tags)
                break
            except self.transient_errors:
                time.sleep(3)
        else:
            return None
        if protect_tags:
            res = [TranslationResult(self.unmask(r.text), r.detected_source_lang) for r in res]
        return res

    def resolve_target(self, raw):
        """Texto digitado -> código de saída válido (grafia do serviço), ou None."""
        return self._target_by_norm.get((raw or "").strip().upper())

    def cache_file(self, stem):
        tag = f"_{self.name}" if self.cache_tag is None else self.cache_tag
        return SCRIPT_DIR / f"{stem}{tag}.json"


@register_provider
class DeepLProvider(TranslationProvider):
    name, label, key_env = "deepl", "DeepL", "DEEPL_KEY"
    cache_tag = ""                             # mantém os arquivos cache.json / deck_cache.json

    def connect(self, key):
        import deepl                           # importado aqui: só quem usa DeepL precisa dele
        self.tr = deepl.Translator(key)
        self.transient_errors = (deepl.ConnectionException,)

    def fetch_target_languages(self):
        return [Language(l.code, l.name) for l in self.tr.get_target_languages()]

    def fetch_source_languages(self):
        return [Language(l.code, l.name) for l in self.tr.get_source_languages()]

    def usage(self):
        return self.tr.get_usage().character

    def _translate(self, texts, target_lang, source_lang, html):
        res = self.tr.translate_text(texts, source_lang=source_lang, target_lang=target_lang,
                                     tag_handling="html" if html else None)
        return [TranslationResult(r.text, r.detected_source_lang) for r in res]


def choose_provider(opts):
    """Serviço de tradução: --provider, ou pergunta (se houver mais de um), ou o padrão."""
    name = getattr(opts, "provider", None)
    if not name and len(PROVIDERS) > 1 and not Ui.auto:
        keys = {str(i): n for i, n in enumerate(sorted(PROVIDERS), 1)}
        default = next(k for k, n in keys.items() if n == DEFAULT_PROVIDER)
        key = Ui.choose("Serviço de tradução:",
                        {k: PROVIDERS[n].label for k, n in keys.items()}, default)
        name = keys[key]
    return make_provider(name or DEFAULT_PROVIDER)


# ======================================================================
# Caches
# ======================================================================
class TranslationCache:
    """cache.json (v3)
    trans: {SAIDA: {ORIGEM: {hash(texto): tradução}}}   ("?" = origem desconhecida, vinda da v2)
    lang:  {hash(texto simples): idioma detectado}
    Só hashes vão para o arquivo: o texto original do baralho não é guardado."""

    VERSION = 3

    def __init__(self, path, valid_target_codes):
        self.path = path
        self.valid_target_codes = valid_target_codes
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
                log(f"cache.json estava ilegível; guardei uma cópia em {bak.name} e comecei um novo")
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
            log(f"cache antigo convertido: {migrated} traduções aproveitadas")
            ver = 2
        if ver == 2:                            # v2: texto original como chave, origem desconhecida
            trans, migrated = {}, 0
            for t_code, d in data.get("trans", {}).items():
                trans[t_code] = {"?": {h(t): out for t, out in d.items()}}
                migrated += len(d)
            data = {"trans": trans}
            log(f"cache v2 convertido para hashes: {migrated} traduções aproveitadas "
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


class DeckNameCache:
    """deck_cache.json, separado do cache.json. Só a saída bruta do serviço é gravada;
    nomes digitados à mão nunca vão para o cache."""

    def __init__(self, path):
        self.path = path
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
            log(f"deck_cache.json estava ilegível; guardei uma cópia em {bak.name}")
        return {"version": 1, "names": {}}

    def bucket(self, target, src_lang):
        return self.data["names"].setdefault(target, {}).setdefault(src_lang or "auto", {})

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


# ======================================================================
# Coleção do Anki
# ======================================================================
class AnkiCollection:
    """Importa o .apkg, expõe notas/campos, aplica traduções e exporta."""

    def __init__(self, src):
        self.src = Path(src)
        self.col = None
        self.notes = []
        self.model_names = {}                                  # mid -> nome do modelo
        self.field_values = collections.defaultdict(list)      # (mid, campo) -> valores com texto

    def import_package(self):
        log(f"importando {self.src} ({self.src.stat().st_size / 1e6:.1f} MB)")
        db_path = Path(tempfile.mkdtemp()) / "collection.anki2"
        self.col = Collection(str(db_path))
        with Heartbeat("importando baralho"):
            self.col.import_anki_package(ImportAnkiPackageRequest(
                package_path=str(self.src), options=ImportAnkiPackageOptions()))

    def load_notes(self):
        for nid in Ui.progress(self.col.find_notes(""), "carregando notas"):
            n = self.col.get_note(nid)
            self.notes.append(n)
            if n.mid not in self.model_names:
                self.model_names[n.mid] = n.note_type()["name"]
            for f, v in n.items():
                if len(TextTools.plain(v)) >= 3:
                    self.field_values[(n.mid, f)].append(v)
        log("notas:", len(self.notes))

    def apply_translations(self, chosen, cache):
        for n in Ui.progress(self.notes, "aplicando traduções"):
            changed = False
            for f, v in n.items():
                c = chosen.get((n.mid, f))
                if c:
                    new = cache.get(c[1], c[0], v)
                    if new is not None and new != v:
                        n[f] = new
                        changed = True
            if changed:
                self.col.update_note(n)

    def deck_list(self):
        return [(d.id, d.name) for d in self.col.decks.all_names_and_ids(
            skip_empty_default=True, include_filtered=False) if d.name != "Default"]

    def notetypes(self):
        return self.col.models.all()

    def note_count(self, mid):
        return len(self.col.find_notes(f"mid:{mid}"))

    def export(self, out_path):
        with Heartbeat("exportando"):
            self.col.export_anki_package(out_path=str(out_path), options=ExportAnkiPackageOptions(
                with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False),
                limit=None)

    def close(self):
        if self.col:
            self.col.close()


# ======================================================================
# Detecção de idioma
# ======================================================================
class FieldLanguageDetector:
    """Detecta o idioma de cada campo por amostragem. Cache por texto (hash do texto simples):
    textos já detectados antes não são reenviados. A amostra prioriza textos conhecidos,
    então só textos realmente novos gastam caracteres."""

    def __init__(self, client, cache, target):
        self.client, self.cache, self.target = client, cache, target

    def detect(self, field_values):
        lang_cache = self.cache.lang
        detected = {}                                    # (mid, campo) -> (idioma, proporção, exemplo)
        sent = reused = 0
        for key, vs in field_values.items():
            uniq = list(dict.fromkeys(TextTools.plain(v) for v in vs))
            known = [p for p in uniq if h(p) in lang_cache]
            unknown = [p for p in uniq if h(p) not in lang_cache]
            pick = random.sample(known, min(SAMPLE, len(known)))
            fresh = random.sample(unknown, min(SAMPLE - len(pick), len(unknown)))
            if fresh:
                if sent == 0:
                    log(f"detectando o idioma dos campos (amostra de até {SAMPLE} textos cada)...")
                langs = self.client.detect(fresh, self.target)
                if langs is None:
                    raise SystemExit(f"{self.client.label} fora do ar; rode de novo "
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
            log(f"detecção de idioma: {sent} textos enviados, {reused} reaproveitados do cache")
        else:
            log("idiomas já detectados antes (cache): nenhum caractere gasto na detecção")
        return detected


# ======================================================================
# Tradução dos campos
# ======================================================================
class FieldTranslator:
    """Traduz os campos escolhidos (cache por saída + origem + hash do texto)."""

    def __init__(self, client, cache):
        self.client, self.cache = client, cache

    def translate(self, chosen, field_values):
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
        log(f"a traduzir: {len(queued)} textos (~{chars} caracteres); "
            f"já estavam no cache (sem custo): {len(cached)}")

        for (L, T), texts in todo.items():
            texts = sorted(texts)
            step = self.client.batch_size
            for i in range(0, len(texts), step):
                chunk = texts[i:i + step]
                res = self.client.translate(chunk, target_lang=T, source_lang=L,
                                            protect_tags=True)
                if res is None:
                    raise SystemExit(f"{self.client.label} fora do ar; rode de novo "
                                     f"(o cache preserva o progresso).")
                for src, r in zip(chunk, res):
                    out = r.text
                    ok = TextTools.PROT.findall(out) == TextTools.PROT.findall(src)
                    if not ok:
                        log(" tag danificada, mantido original:", TextTools.plain(src)[:50])
                    self.cache.put(T, L, src, out if ok else src)
                self.cache.save()
                log(f" [{L}->{T}] {min(i + step, len(texts))}/{len(texts)}")


# ======================================================================
# Renomear baralhos
# ======================================================================
class DeckRenamer:
    """Traduz/altera nomes de baralhos. Traduz segmentos soltos (nunca o caminho completo).
    Baralhos principais são os de primeiro nível; baralhos secundários são os que
    ficam dentro de outro baralho (separados por '::')."""

    def __init__(self, anki, client, target, picked, cache, opts):
        self.opts = opts
        self.anki, self.client = anki, client
        self.target, self.tbase = target, lang_base(target)
        self.picked = picked
        self.cache = cache

    # ---- seleção ----
    @staticmethod
    def parse_numbers(raw, n):
        out = set()
        for tok in re.split(r"[,\s;]+", raw):
            if not tok:
                continue
            m = re.fullmatch(r"(\d+)-(\d+)", tok)
            rng = range(int(m[1]), int(m[2]) + 1) if m else ([int(tok)] if tok.isdigit() else None)
            if rng is None or any(not 1 <= k <= n for k in rng):
                return None
            out.update(rng)
        return out or None

    def pick_scope(self, decks):
        """Devolve o conjunto de prefixos (tuplas de segmentos) cujo nome será traduzido."""
        paths = sorted({tuple(n.split(SEP)) for _, n in decks},
                       key=lambda p: tuple(s.lower() for s in p))
        scope = {"main": "1", "all": "2"}.get(self.mode) or Ui.choose(
                          "Quais baralhos traduzir?",
                          {"1": "Só os baralhos principais (baralhos secundários mantêm o nome)",
                           "2": "Todos os baralhos, principais e secundários",
                           "3": "Escolher por número"}, "2")
        if scope == "1":
            return {p[:1] for p in paths}
        if scope == "2":
            return {p[:i + 1] for p in paths for i in range(len(p))}
        print()
        for i, p in enumerate(paths, 1):
            print(f" {i:>3}) {'  ' * (len(p) - 1)}{p[-1]}")
        while True:
            raw = Ui.ask("Números (ex.: 1,3,5-8; n = cancelar)", "n")
            if raw.lower() in ("n", "nao", "não"):
                return None
            nums = self.parse_numbers(raw, len(paths))
            if nums:
                return {paths[k - 1] for k in nums}      # só o último segmento de cada um muda
            print(" Seleção inválida.")

    # ---- nomes ----
    @staticmethod
    def clean_seg(s, fallback):
        s = s.replace(SEP, ":").strip()
        return s or fallback

    def auto_names(self, segments, src_lang):
        bucket = self.cache.bucket(self.target, src_lang)
        pending = sorted(s for s in segments if s not in bucket)
        log(f"nomes de baralhos: {len(pending)} a traduzir "
            f"({len(segments) - len(pending)} já no cache)")
        step = self.client.batch_size
        for i in range(0, len(pending), step):
            chunk = pending[i:i + step]
            res = self.client.translate(chunk, target_lang=self.target, source_lang=src_lang)
            if res is None:
                log(f"{self.client.label} fora do ar; nomes restantes ficam como estão")
                break
            for s, r in zip(chunk, res):
                already = lang_base(r.detected_source_lang) == self.tbase  # já no idioma de saída
                bucket[s] = s if already else self.clean_seg(r.text, s)
            self.cache.save()
        return {s: bucket.get(s, s) for s in segments}

    def build_newseg(self, selected, method, src_lang):
        ordered = sorted(selected, key=lambda p: tuple(s.lower() for s in p))
        overrides = self.opts.deck_names                 # --deck-name ATUAL=NOVO: sempre vence
        auto = {}
        if method in ("1", "3"):
            auto = self.auto_names({p[-1] for p in selected if p[-1] not in overrides}, src_lang)
        newseg, memo = {}, {}
        for p in ordered:
            seg = p[-1]
            if seg in overrides:
                new = overrides[seg]
            elif method == "1":
                new = auto.get(seg, seg)
            elif method == "0":                          # só os nomes de --deck-name mudam
                new = seg
            else:
                suggestion = memo.get(seg) or (auto.get(seg, seg) if method == "3" else seg)
                while True:
                    new = Ui.ask(f" {SEP.join(p)}", suggestion)
                    if new and SEP not in new:
                        break
                    print(f" Nome vazio ou contendo '{SEP}' não é válido.")
                memo[seg] = new                       # mesmo segmento -> mesma sugestão
            if new != seg:
                newseg[p] = new
        return newseg

    @staticmethod
    def final_name(name, newseg):
        p = tuple(name.split(SEP))
        return SEP.join(newseg.get(p[:i + 1], p[i]) for i in range(len(p)))

    # ---- fluxo principal ----
    def run(self):
        # None = perguntar; none/main/all. --deck-name sozinho implica escopo "all"
        self.mode = mode = self.opts.rename_decks or ("all" if self.opts.deck_names else None)
        decks = self.anki.deck_list()
        if mode == "none" or not decks:
            return
        if mode is None and not Ui.yes("\nTraduzir/alterar nomes de baralhos?", "n"):
            return
        selected = self.pick_scope(decks)
        if not selected:
            return
        src_lang = next(iter(self.picked)) if len(self.picked) == 1 else None
        # método fixo por opção; se o escopo veio por opção, o padrão é automático
        fixed_method = {"auto": "1", "manual": "2", "review": "3", "keep": "0"}.get(
            self.opts.deck_method) or ("0" if self.opts.deck_names else "1" if mode else None)

        while True:
            method = fixed_method or Ui.choose("Como alterar os nomes?",
                               {"1": f"Automático ({self.client.label})",
                                "2": "Manual (você digita; Enter mantém o atual)",
                                "3": f"Revisar um a um (sugestão do {self.client.label}; Enter aceita)"}, "1")
            newseg = self.build_newseg(selected, method, src_lang)
            plan = {did: (old, self.final_name(old, newseg)) for did, old in decks}
            plan = {did: v for did, v in plan.items() if v[0] != v[1]}
            if not plan:
                log("nenhum nome de baralho mudou")
                return

            print("\n=== Nomes de baralhos ===")
            for old, new in sorted(plan.values()):
                print(f" {old}  ->  {new}")

            finals = collections.defaultdict(list)       # Anki ignora maiúsculas/minúsculas
            for did, old in decks:
                finals[(plan[did][1] if did in plan else old).lower()].append(did)
            dups = [k for k, v in finals.items() if len(v) > 1]
            if dups:
                print("\nConflito: mais de um baralho ficaria com o mesmo nome:")
                for k in dups:
                    print(f" {k}")
                if fixed_method or not Ui.yes("Tentar de novo?", "s"):
                    return                               # método fixo daria o mesmo conflito
                continue
            if Ui.yes("\nAplicar?", "s"):
                break
            if not Ui.yes("Tentar de novo?", "n"):
                return

        self._apply(plan)

    def _apply(self, plan):
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
                log(f" não consegui renomear '{old}': {e}")
        log(f"baralhos renomeados: {done}/{len(plan)}")


# ======================================================================
# Aplicação
# ======================================================================
class AnkiTranslatorApp:
    """Orquestra o fluxo: perguntas -> importar -> detectar -> traduzir -> renomear -> exportar."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes
        self.client = choose_provider(opts)
        usage = self.client.usage()
        if usage is not None:
            log(f"uso atual {self.client.label}:", usage)
        self.cache = TranslationCache(self.client.cache_file("cache"), self.client.target_codes)
        self.deck_cache = DeckNameCache(self.client.cache_file("deck_cache"))
        log(f"cache: {self.cache.count()} traduções e {len(self.cache.lang)} idiomas "
            f"de texto guardados em {self.cache.path}")
        self.anki = None
        self.target = self.tbase = None

    # ---- perguntas ----
    def ask_paths(self):
        return ask_paths(self.opts)

    def ask_target(self):
        if self.opts.target:
            target = self.client.resolve_target(self.opts.target)
            if not target:
                raise SystemExit(f"Idioma de saída inválido: {self.opts.target}. "
                                 f"Use o comando 'langs' para ver os códigos.")
        else:
            if not Ui.auto:
                Ui.show_langs("Idiomas de saída disponíveis (código nome):",
                              self.client.target_langs)
            while True:
                raw = Ui.ask("Idioma de saída (Enter = português do Brasil, "
                             "ou digite um código da lista acima)", self.client.default_target)
                target = self.client.resolve_target(raw)
                if target:
                    break
                print(f" '{raw}' não é um código válido. Use um dos códigos da lista acima.")
        self.target, self.tbase = target, lang_base(target)

    def show_detected(self, detected):
        print("\n=== Idioma detectado em cada campo ===")
        cur = None
        for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
            if mid != cur:
                print(f"\n{self.anki.model_names[mid]}")
                cur = mid
            label = lang if ratio >= MIN_RATIO else f"{lang}? (incerto)"
            print(f" {f}: {label} ({ratio:.0%}) ex.: {ex}")

    def _languages_from_args(self, valid):
        raw = (self.opts.lang or "all").upper()
        if raw == "ALL":
            return set(valid)
        picked = {t for t in re.split(r"[,\s;]+", raw) if t}
        bad = sorted(picked - set(valid))
        if bad:
            self.anki.close()
            raise SystemExit(f"Idioma(s) não detectado(s) nos campos: {', '.join(bad)}. "
                             f"Detectados: {', '.join(valid)}")
        return picked

    def ask_languages(self, detected):
        counts = collections.Counter(
            lang for (lang, ratio, _) in detected.values()
            if ratio >= MIN_RATIO and lang_base(lang) != self.tbase)
        already = sum(1 for (lang, ratio, _) in detected.values()
                      if ratio >= MIN_RATIO and lang_base(lang) == self.tbase)
        options = counts.most_common()
        if not options:
            self.anki.close()
            raise SystemExit("\nNenhum campo em outro idioma para traduzir.")

        print(f"\n=== Idiomas que podem ser traduzidos para {self.target} ===")
        for i, (code, c) in enumerate(options, 1):
            print(f" {i}) {code} - {self.client.source_names.get(code, code)} ({c} campos)")
        if already:
            print(f" ({already} campos já estão em {self.tbase} e serão mantidos; "
                  f"campos 'incertos' também)")

        valid = dict(options)
        if self.opts.lang or Ui.auto:                    # sem pergunta: --lang, ou todos com --yes
            return self._languages_from_args(valid)
        default = "1" if len(options) == 1 else ""
        while True:
            prompt = ("\nTraduzir qual idioma? número ou código "
                      "(vários separados por vírgula, n = cancelar)")
            raw = input(f"{prompt}{f' [{default}]' if default else ''}: "
                        ).strip().strip('"').upper() or default
            if raw in ("N", "NAO", "NÃO"):
                self.anki.close()
                raise SystemExit("Cancelado.")
            picked, bad = set(), []
            for tok in re.split(r"[,\s;]+", raw):
                if not tok:
                    continue
                if tok.isdigit() and 1 <= int(tok) <= len(options):
                    picked.add(options[int(tok) - 1][0])
                elif tok in valid:
                    picked.add(tok)
                else:
                    bad.append(tok)
            if picked and not bad:
                return picked
            print(f" Opção inválida: {', '.join(bad) or raw}. "
                  f"Escolha entre os números/códigos listados.")

    # ---- fluxo ----
    def run(self):
        src, out = self.ask_paths()
        self.ask_target()

        self.anki = AnkiCollection(src)
        self.anki.import_package()
        self.anki.load_notes()

        detector = FieldLanguageDetector(self.client, self.cache, self.target)
        detected = detector.detect(self.anki.field_values)
        self.show_detected(detected)

        picked = self.ask_languages(detected)
        chosen = {k: (lang, self.target) for k, (lang, ratio, _) in detected.items()
                  if lang in picked and ratio >= MIN_RATIO}
        log("idiomas escolhidos:", ", ".join(sorted(picked)), f"-> {len(chosen)} campos")

        FieldTranslator(self.client, self.cache).translate(chosen, self.anki.field_values)
        self.anki.apply_translations(chosen, self.cache)

        DeckRenamer(self.anki, self.client, self.target, picked, self.deck_cache,
                    self.opts).run()

        self.anki.export(out)
        self.anki.close()
        log("pronto ->", out)
        usage = self.client.usage()
        if usage is not None:
            log(f"uso {self.client.label} ao final:", usage)


# ======================================================================
# Inverter cartões (comando separado, sem serviço de tradução)
# ======================================================================
def ask_input(opts):
    """Pergunta (ou lê das opções) o .apkg de entrada."""
    src = Path(opts.input or Ui.ask("Arquivo de entrada", "italian.apkg")).resolve()
    if not src.exists():
        raise SystemExit(f"Arquivo não encontrado: {src}")
    return src


def ask_paths(opts, suffix="_out"):
    """Pergunta (ou lê das opções) o .apkg de entrada e o de saída."""
    src = ask_input(opts)
    out = Path(opts.output or Ui.ask("Arquivo de saída", f"{src.stem}{suffix}.apkg")).resolve()
    if out.suffix.lower() != ".apkg":
        out = out.with_name(out.name + ".apkg")
    return src, out


class CardReverser:
    """Inverte frente e verso trocando, nos templates dos cartões, as referências a PARES de
    campos ({{Palavra}} <-> {{Tradução}}). Vários pares são trocados de uma vez, o que é
    necessário quando cada lado tem campos de áudio, frase etc. O conteúdo das notas não
    muda e a operação é reversível: rodar de novo desfaz. Só entram tipos de nota padrão com
    2+ campos (o tipo cloze, que tem um campo único, fica de fora)."""

    REF = re.compile(r"\{\{([#^/]?)((?:[^{}:]*:)*)([^{}:]+)\}\}")
    STANDARD = 0                                          # no Anki, cloze é 1

    def __init__(self, anki, opts):
        self.anki, self.opts = anki, opts
        self.counts = {}                                  # id do tipo de nota -> nº de notas

    # ---- templates ----
    @classmethod
    def swap_refs(cls, tmpl, mapping):
        """Troca as referências a campos num template, numa única passada, conforme
        'mapping' ({A: B, B: A}). Preserva filtros e seções: {{type:A}}, {{#A}}...{{/A}}."""
        def sub(m):
            prefix, filters, name = m.groups()
            new = mapping.get(name.strip())
            if new is None:
                return m.group(0)
            return "{{" + prefix + filters + new + "}}"
        return cls.REF.sub(sub, tmpl)

    @classmethod
    def refs(cls, tmpl, names):
        """Campos citados no template, na ordem em que aparecem."""
        seen = []
        for m in cls.REF.finditer(tmpl):
            n = m.group(3).strip()
            if n in names and n not in seen:
                seen.append(n)
        return seen

    @classmethod
    def layout(cls, nt):
        """(campos mostrados na frente, campos que só aparecem no verso), pelo 1º template."""
        names = [f["name"] for f in nt["flds"]]
        t = nt["tmpls"][0]
        front = cls.refs(t["qfmt"], names)
        back = [n for n in cls.refs(t["afmt"], names) if n not in front]
        return front, back

    @classmethod
    def suggest_pairs(cls, nt):
        """Pares de campos a trocar. 1º: por nome, quando o nome de um campo termina com o
        de outro ('Word' / 'TranslationWord', 'WordAudio' / 'TranslationWordAudio').
        2º: pelo layout do template (1º campo da frente / 1º campo só do verso).
        3º: os dois primeiros campos."""
        names = [f["name"] for f in nt["flds"]]
        pairs, used = [], set()
        for a in names:
            if a in used:
                continue
            cands = [b for b in names if b not in used and len(b) > len(a)
                     and b.lower().endswith(a.lower())]
            if len(cands) == 1:
                pairs.append((a, cands[0]))
                used.update((a, cands[0]))
        if pairs:
            return pairs
        front, back = cls.layout(nt)
        if front and back:
            return [(front[0], back[0])]
        return [(names[0], names[1])]

    @staticmethod
    def parse_pairs(raw, names):
        """'A=B, C=D' -> [(A, B), (C, D)] com a grafia real dos campos; None se inválido
        (campo inexistente, repetido ou pareado consigo mesmo)."""
        lower = {n.lower(): n for n in names}
        pairs, used = [], set()
        for item in re.split(r"[,;]+", raw):
            if not item.strip():
                continue
            parts = [p.strip().lower() for p in re.split(r"<->|=", item)]
            if (len(parts) != 2 or parts[0] == parts[1] or used & set(parts)
                    or any(p not in lower for p in parts)):
                return None
            used.update(parts)
            pairs.append((lower[parts[0]], lower[parts[1]]))
        return pairs or None

    # ---- seleção ----
    def eligible(self):
        out, skipped = [], 0
        for nt in self.anki.notetypes():
            n = self.anki.note_count(nt["id"])
            if n == 0:
                continue                                  # tipos padrão do Anki sem notas
            self.counts[nt["id"]] = n
            if nt["type"] == self.STANDARD and len(nt["flds"]) >= 2:
                out.append(nt)
            else:
                skipped += 1
        if skipped:
            log(f"tipos de nota ignorados (cloze ou com menos de 2 campos): {skipped}")
        return sorted(out, key=lambda nt: nt["name"].lower())

    def pick_notetypes(self, cands):
        if self.opts.notetype:
            wanted = {n.lower(): n for n in self.opts.notetype}
            chosen = [nt for nt in cands if nt["name"].lower() in wanted]
            missing = set(wanted) - {nt["name"].lower() for nt in chosen}
            if missing:
                self.anki.close()
                raise SystemExit(
                    f"Tipo(s) de nota não encontrado(s): {', '.join(wanted[m] for m in sorted(missing))}. "
                    f"Disponíveis: {', '.join(nt['name'] for nt in cands)}")
            return chosen
        if Ui.auto:
            return cands
        print("\n=== Tipos de nota que podem ser invertidos ===")
        for i, nt in enumerate(cands, 1):
            print(f" {i}) {nt['name']} ({self.counts[nt['id']]} notas, "
                  f"{len(nt['flds'])} campos)")
        while True:
            raw = Ui.ask("\nQuais inverter? (todos, ou números ex.: 1,3; n = cancelar)", "todos")
            if raw.lower() in ("n", "nao", "não"):
                return []
            if raw.lower() in ("todos", "all", "t"):
                return cands
            nums = DeckRenamer.parse_numbers(raw, len(cands))
            if nums:
                return [cands[k - 1] for k in sorted(nums)]
            print(" Seleção inválida.")

    def show_layout(self, nt, pairs):
        front, back = self.layout(nt)
        paired = {n for p in pairs for n in p}
        stay = [f["name"] for f in nt["flds"] if f["name"] not in paired]
        print(f"\n {nt['name']} ({self.counts[nt['id']]} notas)")
        print(f"   Frente hoje: {', '.join(front) or '-'}")
        print(f"   Verso hoje:  {', '.join(back) or '-'}")
        print("   Troca sugerida (cada campo passa a ocupar o lugar do outro):")
        for a, b in pairs:
            print(f"     {a}  <->  {b}")
        if stay:
            print(f"   Não mudam de lado: {', '.join(stay)}")

    def pick_pairs(self, nt):
        names = [f["name"] for f in nt["flds"]]
        if self.opts.swap:
            pairs = self.parse_pairs(",".join(self.opts.swap), names)
            if not pairs:
                log(f" {nt['name']}: não tem os campos de --swap; ignorado")
            return pairs
        pairs = self.suggest_pairs(nt)
        if Ui.auto or len(names) == 2:                    # com 2 campos não há o que escolher
            return pairs
        self.show_layout(nt, pairs)
        while True:
            raw = Ui.ask(" Aceitar a troca sugerida? (n = pular este tipo, "
                         "ou digite os pares: A=B, C=D)", "ok")
            if raw.lower() in ("ok", "s", "sim", "y", "yes"):
                return pairs
            if raw.lower() in ("n", "nao", "não"):
                return None
            custom = self.parse_pairs(raw, names)
            if custom:
                return custom
            print(f" Pares inválidos. Use nomes de campos deste tipo: {', '.join(names)}")

    # ---- aplicação ----
    def apply(self, plan):
        models = self.anki.col.models
        save = getattr(models, "update_dict", None) or models.save
        done = 0
        for nt, pairs in plan:
            mapping = {}
            for a, b in pairs:
                mapping[a], mapping[b] = b, a
            changed = False
            for t in nt["tmpls"]:
                q, ans = self.swap_refs(t["qfmt"], mapping), self.swap_refs(t["afmt"], mapping)
                changed |= (q != t["qfmt"] or ans != t["afmt"])
                t["qfmt"], t["afmt"] = q, ans
            if not changed:
                log(f" {nt['name']}: nenhum template usa os campos escolhidos; ignorado")
                continue
            save(nt)
            done += 1
        log(f"tipos de nota invertidos: {done}/{len(plan)}")
        return done

    def run(self):
        """Devolve quantos tipos de nota foram invertidos."""
        cands = self.eligible()
        if not cands:
            log("nenhum tipo de nota com frente e verso para inverter")
            return 0
        plan = []
        for nt in self.pick_notetypes(cands):
            pairs = self.pick_pairs(nt)
            if pairs:
                plan.append((nt, pairs))
        if not plan:
            return 0
        print("\n=== Inversão de cartões ===")
        for nt, pairs in plan:
            print(f" {nt['name']} ({self.counts[nt['id']]} notas, "
                  f"{len(nt['tmpls'])} template(s)):")
            for a, b in pairs:
                print(f"   {a}  <->  {b}")
        if not Ui.yes("\nAplicar?", "s"):
            return 0
        return self.apply(plan)


class ReverseApp:
    """Comando reverse: importar -> inverter cartões -> exportar. Não usa serviço de tradução."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes

    def run(self):
        src, out = ask_paths(self.opts, suffix="_reversed")
        anki = AnkiCollection(src)
        anki.import_package()
        if not CardReverser(anki, self.opts).run():
            anki.close()
            raise SystemExit("Nada foi invertido; nenhum arquivo gerado.")
        anki.export(out)
        anki.close()
        log("pronto ->", out)


class InfoApp:
    """Comando info: mostra tipos de nota, campos e baralhos do .apkg (não gera arquivo)."""

    def __init__(self, opts):
        self.opts = opts

    def run(self):
        anki = AnkiCollection(ask_input(self.opts))
        anki.import_package()
        print("\n=== Tipos de nota ===")
        for nt in sorted(anki.notetypes(), key=lambda nt: nt["name"].lower()):
            n = anki.note_count(nt["id"])
            if n == 0:
                continue                                  # tipos padrão do Anki sem notas
            kind = "cloze" if nt["type"] == 1 else "padrão"
            print(f" {nt['name']} [{kind}] - {n} notas, {len(nt['tmpls'])} template(s)")
            print(f"   campos: {', '.join(f['name'] for f in nt['flds'])}")
            if nt["type"] == CardReverser.STANDARD and len(nt["flds"]) >= 2:
                front, back = CardReverser.layout(nt)
                print(f"   frente: {', '.join(front) or '-'}")
                print(f"   verso:  {', '.join(back) or '-'}")
                swap = ", ".join(f"{x}={y}" for x, y in CardReverser.suggest_pairs(nt))
                print(f"   troca sugerida (--swap): {swap}")
        print("\n=== Baralhos ===")
        for _, name in sorted(anki.deck_list(), key=lambda d: d[1].lower()):
            parts = name.split(SEP)
            print(f" {'  ' * (len(parts) - 1)}{parts[-1]}")
        anki.close()


# ======================================================================
# Linha de comando
# ======================================================================
COMMANDS = ("translate", "reverse", "info", "langs")
MENU = {"1": ("translate", "Traduzir baralho (serviço de tradução)"),
        "2": ("reverse", "Inverter cartões (frente <-> verso)"),
        "3": ("info", "Ver informações do baralho (tipos de nota, campos, baralhos)")}


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "translate")                      # atalho: script deck.apkg -t PT-BR

    p = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Ferramentas para baralhos .apkg do Anki. Sem nenhum argumento, abre o "
                    "modo interativo (menu). Use 'COMANDO -h' para ver as opções de cada comando.",
        epilog="Em qualquer comando, o que não for informado por opção é perguntado, "
               "a menos que -y/--yes seja usado.")
    sub = p.add_subparsers(dest="command", metavar="COMANDO")

    io = argparse.ArgumentParser(add_help=False)
    io.add_argument("input", nargs="?", metavar="ENTRADA", help="arquivo .apkg de entrada")
    run_opts = argparse.ArgumentParser(add_help=False)
    run_opts.add_argument("-o", "--output", metavar="SAIDA", help="arquivo .apkg de saída")
    run_opts.add_argument("-y", "--yes", action="store_true",
                          help="não perguntar nada: usa os padrões de cada comando")

    t = sub.add_parser(
        "translate", parents=[io, run_opts],
        help="traduz os campos (e, opcionalmente, os nomes dos baralhos)",
        description="Traduz os campos de um baralho com um serviço de tradução (--provider). "
                    "Saída padrão: <entrada>_out.apkg. "
                    "Com -y: PT-BR, todos os idiomas detectados, nomes de baralhos intactos.")
    t.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICO",
                   help=f"serviço de tradução: {', '.join(sorted(PROVIDERS))} "
                        f"(padrão: {DEFAULT_PROVIDER})")
    t.add_argument("-t", "--target", metavar="CODIGO",
                   help="idioma de saída, ex.: PT-BR, EN-US (veja o comando 'langs')")
    t.add_argument("-l", "--lang", metavar="CODIGOS",
                   help="idioma(s) de origem a traduzir, separados por vírgula "
                        "(ex.: IT,EN) ou 'all' para todos os detectados")
    t.add_argument("-d", "--rename-decks", choices=["none", "main", "all"],
                   help="nomes de baralhos: none = não alterar, main = só os baralhos "
                        "principais, all = principais e secundários")
    t.add_argument("-m", "--deck-method", choices=["auto", "manual", "review", "keep"],
                   help="como renomear baralhos: auto (pelo serviço), manual (você digita), "
                        "review (sugestão do serviço, um a um) ou keep (só os de --deck-name)")
    t.add_argument("--deck-name", action="append", metavar="ATUAL=NOVO",
                   help="renomeia o baralho de nome ATUAL (um nível, sem '::') para NOVO; "
                        "pode repetir. Sem -d/-m, só estes são renomeados")

    r = sub.add_parser(
        "reverse", parents=[io, run_opts],
        help="inverte frente e verso dos cartões (sem tradução)",
        description="Inverte frente e verso trocando os campos nos templates dos cartões. "
                    "Saída padrão: <entrada>_reversed.apkg. Rodar de novo desfaz. "
                    "Com -y: todos os tipos de nota (cloze não entra).")
    r.add_argument("--notetype", action="append", metavar="NOME",
                   help="inverte só este tipo de nota (pode repetir)")
    r.add_argument("--swap", action="append", metavar="CAMPO1=CAMPO2",
                   help="par de campos que trocam de lado; pode repetir (padrão: pares "
                        "sugeridos para cada tipo de nota; veja o comando 'info')")

    sub.add_parser("info", parents=[io],
                   help="mostra tipos de nota, campos e baralhos de um .apkg",
                   description="Mostra tipos de nota, campos e baralhos de um .apkg "
                               "(útil para escolher --notetype e --swap). Não gera arquivo.")
    lg = sub.add_parser("langs", help="lista os idiomas de saída do serviço de tradução")
    lg.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICO",
                    help=f"serviço de tradução (padrão: {DEFAULT_PROVIDER})")

    opts = p.parse_args(argv)

    if opts.command == "translate":
        opts.deck_names = {}
        for item in opts.deck_name or []:
            old, eq, new = item.partition("=")
            old, new = old.strip(), new.strip()
            if not eq or not old or not new or SEP in old or SEP in new:
                t.error(f"--deck-name inválido: '{item}'. Use ATUAL=NOVO (um nível, sem '{SEP}')")
            opts.deck_names[old] = new
        if opts.deck_names and opts.rename_decks == "none":
            t.error("--deck-name não combina com --rename-decks none")
        if opts.deck_method == "keep" and not opts.deck_names:
            t.error("--deck-method keep exige pelo menos um --deck-name")
    elif opts.command == "reverse":
        seen = set()
        for item in opts.swap or []:
            parts = [x.strip().lower() for x in item.split("=")]
            if len(parts) != 2 or not all(parts) or parts[0] == parts[1] or seen & set(parts):
                r.error(f"--swap inválido: '{item}'. Use CAMPO1=CAMPO2, sem repetir campos")
            seen.update(parts)
    return opts


def main(argv=None):
    opts = parse_args(argv)
    if opts.command is None:                             # modo interativo: menu de operações
        key = Ui.choose("O que deseja fazer?", {k: v[1] for k, v in MENU.items()}, "1")
        opts = parse_args([MENU[key][0]])
    Ui.auto = getattr(opts, "yes", False)
    if opts.command == "reverse":
        ReverseApp(opts).run()
    elif opts.command == "info":
        InfoApp(opts).run()
    elif opts.command == "langs":
        Ui.show_langs("Idiomas de saída disponíveis (código nome):",
                      choose_provider(opts).target_langs)
    else:
        AnkiTranslatorApp(opts).run()


if __name__ == "__main__":
    main()
