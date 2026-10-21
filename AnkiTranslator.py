# pip install anki (e deepl, se for usar o serviço DeepL)

import argparse, json, os, re, sys, time, random, tempfile, collections, hashlib, threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ImportAnkiPackageOptions, ExportAnkiPackageOptions)

SCRIPT_DIR = Path(__file__).resolve().parent
SAMPLE, MIN_RATIO = 10, 0.6
DEFAULT_PROVIDER = "deepl"
SEP = "::"
VERSION = "1.1.0"


class AnkiToolError(Exception):
    """Erro esperado (mensagem para a pessoa). O front-end decide como mostrar/sair."""

class ConfigError(AnkiToolError):
    """Opção/entrada inválida."""

class ProviderUnavailable(AnkiToolError):
    """O serviço de tradução não respondeu."""

class Cancelled(AnkiToolError):
    """A operação foi cancelada."""

@dataclass
class RenameConfig:
    mode: str = None                 # None = perguntar | none | main | all
    method: str = None               # auto | manual | review | keep
    names: dict = field(default_factory=dict)      # ATUAL -> NOVO

@dataclass
class ReverseConfig:
    notetypes: list = None           # nomes de tipos de nota, ou None = todos/perguntar
    swap: list = None                # ["Campo1=Campo2", ...]

class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    @property
    def cancelled(self):
        return self._ev.is_set()

class Reporter:
    """Canal de feedback do núcleo. Esta base é silenciosa (ideal para uso como biblioteca);
    a CLI usa ConsoleReporter e a GUI terá o seu."""

    def __init__(self, cancel=None):
        self.cancel = cancel or CancelToken()

    def log(self, *a): pass
    def warn(self, *a): pass
    def advance(self, label, done, total): pass

    def check_cancelled(self):
        if self.cancel.cancelled:
            raise Cancelled("Cancelado.")

def track(rep, items, label):
    """Percorre 'items' avisando o Reporter do andamento (e respeitando o cancelamento)."""
    total = len(items)
    for i, x in enumerate(items, 1):
        rep.check_cancelled()
        yield x
        rep.advance(label, i, total)

class ConsoleReporter(Reporter):
    def __init__(self, quiet=False, cancel=None):
        super().__init__(cancel)
        self.quiet = quiet
        self._last, self._next = {}, {}

    def log(self, *a):
        if not self.quiet:
            print(time.strftime("%H:%M:%S"), *a, flush=True)

    def warn(self, *a):                                   # aparece mesmo com --quiet
        print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)

    def advance(self, label, done, total):
        if self.quiet or not total:
            return
        if sys.stdout.isatty():
            now = time.time()
            if now - self._last.get(label, 0.0) >= 0.2 or done == total:
                print(f"\r{time.strftime('%H:%M:%S')} {label}: {done}/{total} "
                      f"({done * 100 // total}%)", end="", flush=True)
                self._last[label] = now
            if done == total:
                print()
        else:
            nxt = self._next.get(label, 0.1)
            if done / total >= nxt or done == total:
                self.log(f"{label}: {done}/{total} ({done * 100 // total}%)")
                while nxt <= done / total:
                    nxt += 0.1
            self._next[label] = nxt
        if done == total:
            self._last.pop(label, None)
            self._next.pop(label, None)

# ======================================================================
# Utilitários de console
# ======================================================================
class Ui:
    """Entrada/saída de console: log, perguntas, menus e barra de progresso.
    Com Ui.auto = True (opção --yes), nenhuma pergunta é feita: vale o valor padrão."""

    auto = False

    @staticmethod
    def ask(msg, default):
        if Ui.auto:
            return default
        return input(f"{msg} [{default}]: ").strip().strip('"') or default

    @staticmethod
    def yes(msg, default="n"):
        """Pergunta de sim/não. Só aceita s/n (ou variações); Enter usa o padrão."""
        if Ui.auto:
            return default.lower() in ("s", "sim", "y", "yes")
        padrao = "sim" if default.lower() in ("s", "sim", "y", "yes") else "não"
        while True:
            r = input(f"{msg}\n  Digite s (sim) ou n (não). Só Enter = {padrao}: ").strip().lower()
            if r == "":
                r = default.lower()
            if r in ("s", "sim", "y", "yes"):
                return True
            if r in ("n", "nao", "não", "no"):
                return False
            print("  Não entendi. Digite s para sim ou n para não.")

    @staticmethod
    def choose(title, opts, default):
        """Menu de UMA escolha. opts: {"1": "Texto"} ou {"1": ("Texto", "explicação curta")}."""
        print(f"\n{title}")
        for k, v in opts.items():
            label, desc = v if isinstance(v, tuple) else (v, None)
            mark = "   <- padrão (só apertar Enter)" if k == default else ""
            print(f"  {k}) {label}{mark}")
            if desc:
                print(f"       {desc}")
        print("\nDigite o número da opção e aperte Enter.")
        while True:
            r = Ui.ask("Escolha", default)
            if r in opts:
                return r
            print(f"  Opção inválida. Digite um destes números: {', '.join(opts)}.")

    @staticmethod
    def parse_numbers(raw, n):
        """'1,3,5-8' -> {1,3,5,6,7,8}; None se algo estiver fora de 1..n."""
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

    @staticmethod
    def select(title, labels, multi=True, default="all", intro=None, hint=None,
               aliases=None, cancel=True):
        """Lista numerada para escolher itens. Devolve lista de índices (a partir de 0),
        ou None se a pessoa cancelou (0).
        default: 'all' (Enter = todos), 'first' (Enter = o 1) ou None (Enter não vale).
        aliases: {TEXTO_EM_MAIUSCULAS: índice}, para aceitar também códigos digitados."""
        n = len(labels)
        if Ui.auto:
            return list(range(n)) if default == "all" else [0] if default == "first" else None
        print(f"\n{title}")
        if intro:
            print(intro)
        print()
        for i, lab in enumerate(labels, 1):
            print(f"  {i:>3}) {lab}")
        if cancel:
            print("    0) Cancelar (não fazer nada)")
        print()
        if multi:
            print("Como escolher: digite o número do item e aperte Enter.")
            print("Para escolher vários, separe por vírgula (ex.: 1,3) ou use intervalo (ex.: 2-5).")
        else:
            print("Como escolher: digite o número do item e aperte Enter.")
        if hint:
            print(hint)
        if default == "all":
            print("Se você só apertar Enter, eu escolho TODOS os itens.")
        elif default == "first":
            print("Se você só apertar Enter, eu escolho o item 1.")
        while True:
            raw = input("Sua escolha: ").strip().strip('"')
            if raw == "":
                if default == "all":
                    return list(range(n))
                if default == "first":
                    return [0]
                print("  Preciso de uma resposta. Digite um número da lista.")
                continue
            if cancel and raw == "0":
                return None
            picked, bad = set(), []
            for tok in re.split(r"[,\s;]+", raw):
                if not tok:
                    continue
                if aliases and tok.upper() in aliases:
                    picked.add(aliases[tok.upper()])
                    continue
                nums = Ui.parse_numbers(tok, n)
                if nums:
                    picked.update(k - 1 for k in nums)
                else:
                    bad.append(tok)
            if picked and not bad and (multi or len(picked) == 1):
                return sorted(picked)
            if picked and not bad:
                print("  Aqui dá para escolher só um item. Digite um único número.")
            else:
                print(f"  Não entendi '{', '.join(bad) or raw}'. Digite números de 1 a {n}"
                      f"{', ou 0 para cancelar' if cancel else ''}.")

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



# ======================================================================
# Seletor de arquivos (em texto)
# ======================================================================
def default_start():
    """Pasta inicial do seletor: a pasta atual, ou a do script, se só ali houver .apkg."""
    def has_apkg(d):
        try:
            return any(p.suffix.lower() == ".apkg" for p in d.iterdir() if p.is_file())
        except OSError:
            return False
    cwd = Path.cwd()
    if has_apkg(cwd) or not has_apkg(SCRIPT_DIR):
        return cwd
    return SCRIPT_DIR

def pick_file(start=None, suffix=".apkg"):
    """Navegador de arquivos no terminal. Mostra as pastas e os arquivos .apkg da pasta atual.
    Devolve o Path do arquivo escolhido, ou None se a pessoa cancelou."""
    cur = Path(start or Path.cwd()).resolve()
    while True:
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            print(f"\n  Não consegui abrir a pasta {cur}. Voltando uma pasta.")
            if cur.parent == cur:
                return None
            cur = cur.parent
            continue

        dirs, files = [], []
        for p in entries:
            try:
                if p.is_dir() and not p.name.startswith("."):
                    dirs.append(p)
                elif p.is_file() and p.suffix.lower() == suffix:
                    files.append(p)
            except OSError:
                continue

        print(f"\n=== Escolher arquivo {suffix} ===")
        print(f"Você está na pasta: {cur}\n")
        items = []
        if cur.parent != cur:
            print("   ..) Subir uma pasta (voltar)")
        for p in dirs:
            items.append(p)
            print(f"  {len(items):>3}) [pasta]  {p.name}")
        for p in files:
            items.append(p)
            try:
                size = f"  ({p.stat().st_size / 1e6:.1f} MB)"
            except OSError:
                size = ""
            print(f"  {len(items):>3}) {p.name}{size}")
        if not items:
            print(f"      (aqui não há pastas nem arquivos {suffix})")
        print("    0) Cancelar (não escolher nenhum arquivo)")

        print("\nComo usar:")
        print("  - Número de uma [pasta]: entra nessa pasta.")
        print(f"  - Número de um arquivo {suffix}: escolhe esse arquivo.")
        print("  - Dois pontos (..): sobe uma pasta.")
        print(f"  - Ou cole o caminho completo de um arquivo {suffix} (ou de uma pasta).")
        raw = input("Sua escolha: ").strip().strip('"').strip("'")

        if raw == "0":
            return None
        if raw == "..":
            cur = cur.parent
            continue
        if raw.isdigit():
            k = int(raw)
            if 1 <= k <= len(items):
                choice = items[k - 1]
                if choice.is_dir():
                    cur = choice
                    continue
                return choice
            faixa = f"de 1 a {len(items)}, " if items else ""
            print(f"\n  Número fora da lista. Digite um número {faixa}'..' ou 0.".replace("um número de", "um número de"))
            continue
        if raw:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = cur / p
            try:
                p = p.resolve()
                if p.is_dir():
                    cur = p
                    continue
                if p.is_file() and p.suffix.lower() == suffix:
                    return p
            except OSError:
                pass
            print(f"\n  Não achei '{raw}' como pasta nem como arquivo {suffix}.")
            continue
        print("\n  Preciso de uma resposta. Digite um número, '..' ou 0.")

class Heartbeat:
    """Para chamadas longas sem progresso interno (importar/exportar):
    mostra 'ainda trabalhando... Ns' a cada poucos segundos."""
    def __init__(self, rep, msg, every=5):
        self.rep, self.msg, self.every = rep, msg, every

    def __enter__(self):
        self.t0, self.stop = time.time(), threading.Event()
        self.rep.log(self.msg + "...")
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while not self.stop.wait(self.every):
            self.rep.log(f"  {self.msg}... {int(time.time() - self.t0)}s")

    def __exit__(self, exc_type, *rest):
        self.stop.set()
        if exc_type is None:
            self.rep.log(f"{self.msg}: concluído em {time.time() - self.t0:.1f}s")
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
        raise ConfigError(f"Chave do {label} não encontrada. Defina a variável de ambiente "
                          f"{env_name} ou crie um arquivo .env ao lado do script com:\n"
                          f"{env_name}=sua-chave-aqui")
    return key

PROVIDERS = {}                                 # nome -> classe; preenchido por @register_provider

def register_provider(cls):
    PROVIDERS[cls.name] = cls
    return cls

def make_provider(name):
    if name not in PROVIDERS:
        raise ConfigError(f"Serviço de tradução desconhecido: {name}. "
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

    def cache_file(self, stem, cache_dir):
        tag = f"_{self.name}" if self.cache_tag is None else self.cache_tag
        return Path(cache_dir) / f"{stem}{tag}.json"

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
        key = Ui.choose("Qual serviço de tradução você quer usar?",
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

class DeckNameCache:
    """deck_cache.json, separado do cache.json. Só a saída bruta do serviço é gravada;
    nomes digitados à mão nunca vão para o cache."""

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
            self.rep.log(f"deck_cache.json estava ilegível; guardei uma cópia em {bak.name}")
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

    def __init__(self, src, keep_scheduling=False, legacy=True, rep=None):
        self.rep = rep or Reporter()
        self.src = Path(src)
        self.keep_scheduling, self.legacy = keep_scheduling, legacy
        self.col = None
        self.notes = []
        self.model_names = {}                                  # mid -> nome do modelo
        self.field_values = collections.defaultdict(list)      # (mid, campo) -> valores com texto

    def import_package(self):
        self.rep.log(f"importando {self.src} ({self.src.stat().st_size / 1e6:.1f} MB)")
        db_path = Path(tempfile.mkdtemp()) / "collection.anki2"
        self.col = Collection(str(db_path))
        with Heartbeat(self.rep, "importando baralho"):
            self.col.import_anki_package(ImportAnkiPackageRequest(
                package_path=str(self.src), options=ImportAnkiPackageOptions(
                    with_scheduling=self.keep_scheduling,
                    with_deck_configs=self.keep_scheduling)))

    def load_notes(self):
        for nid in track(self.rep, self.col.find_notes(""), "carregando notas"):
            n = self.col.get_note(nid)
            self.notes.append(n)
            if n.mid not in self.model_names:
                self.model_names[n.mid] = n.note_type()["name"]
            for f, v in n.items():
                if len(TextTools.plain(v)) >= 3:
                    self.field_values[(n.mid, f)].append(v)
        self.rep.log("notas:", len(self.notes))

    def filter_fields(self, only, skip):
        """Tira de field_values os campos que não devem ser traduzidos (--field / --skip-field).
        Cada item é (tipo_de_nota ou None, campo). Devolve os itens de 'only' sem correspondência."""
        def match(specs, key):
            mid, f = key
            return any(fld.lower() == f.lower()
                       and (nt is None or nt.lower() == self.model_names[mid].lower())
                       for nt, fld in specs)
        keys = list(self.field_values)
        unmatched = [sp for sp in only if not any(match([sp], k) for k in keys)]
        for k in keys:
            if (only and not match(only, k)) or (skip and match(skip, k)):
                del self.field_values[k]
        return unmatched

    def apply_translations(self, chosen, cache):
        for n in track(self.rep, self.notes, "aplicando traduções"):
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
        with Heartbeat(self.rep, "exportando"):
            self.col.export_anki_package(out_path=str(out_path), options=ExportAnkiPackageOptions(
                with_scheduling=self.keep_scheduling, with_deck_configs=self.keep_scheduling,
                with_media=True, legacy=self.legacy),
                limit=None)

    def close(self):
        if self.col:
            self.col.close()
            self.col = None

# ======================================================================
# Detecção de idioma
# ======================================================================
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

# ======================================================================
# Tradução dos campos
# ======================================================================
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
        self.rep.log(f"a traduzir: {len(queued)} textos (~{chars} caracteres); "
            f"já estavam no cache (sem custo): {len(cached)}")

        if dry_run:
            print("\n=== Simulação (--dry-run) ===")
            print(f"Textos que seriam enviados: {len(queued)}")
            print(f"Caracteres estimados: ~{chars}")
            print(f"Já no cache (sem custo): {len(cached)}")
            for (L, T), texts in todo.items():
                print(f"  {L} -> {T}: {len(texts)} textos")
            return chars

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

# ======================================================================
# Renomear baralhos
# ======================================================================
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

class DeckRenameCli:
    """Perguntas e textos do renomeio de baralhos. Toda a lógica está em DeckRenamer."""

    def __init__(self, ren, cfg):
        self.ren, self.cfg = ren, cfg

    def pick_scope(self, paths, mode):
        """Devolve o conjunto de prefixos (tuplas de segmentos) cujo nome será traduzido."""
        scope = {"main": "1", "all": "2"}.get(mode) or Ui.choose(
            "Quais baralhos você quer renomear?",
            {"1": ("Só os baralhos principais",
                   "Os baralhos que ficam DENTRO de outro baralho mantêm o nome de hoje."),
             "2": ("Todos os baralhos",
                   "Os principais e os que ficam dentro deles."),
             "3": ("Eu escolho numa lista",
                   "Você vê a lista e digita os números dos baralhos que quer renomear."),
             "0": ("Cancelar", "Não mexer nos nomes dos baralhos.")}, "2")
        if scope == "0":
            return None
        if scope in ("1", "2"):
            return DeckRenamer.scope_for("main" if scope == "1" else "all", paths)
        labels = ["  " * (len(p) - 1) + p[-1] for p in paths]
        idx = Ui.select("Quais baralhos você quer renomear?", labels, multi=True, default=None,
                        intro="(Os baralhos mais para a direita ficam dentro do baralho de cima.)")
        if idx is None:
            return None
        return {paths[k] for k in idx}               # só o último segmento de cada um muda

    def build_newseg(self, selected, method, src_lang):
        ren, overrides = self.ren, self.cfg.names    # --deck-name ATUAL=NOVO: sempre vence
        if method in ("0", "1"):                     # sem perguntas ("0": só os de --deck-name)
            return ren.auto_newseg(selected, overrides, src_lang, translate=(method == "1"))
        auto = {}
        if method == "3":
            auto = ren.auto_names({p[-1] for p in selected if p[-1] not in overrides}, src_lang)
        if not Ui.auto:
            print("\nVou mostrar um baralho de cada vez.")
            print("O nome entre [colchetes] é o que será usado se você só apertar Enter.")
            print("Para usar outro nome, digite o nome novo e aperte Enter.\n")
        newseg, memo = {}, {}
        for p in sorted(selected, key=lambda p: tuple(s.lower() for s in p)):
            seg = p[-1]
            if seg in overrides:
                new = overrides[seg]
            else:
                suggestion = memo.get(seg) or (auto.get(seg, seg) if method == "3" else seg)
                while True:
                    new = Ui.ask(f"  Baralho '{SEP.join(p)}' - novo nome", suggestion)
                    if new and SEP not in new:
                        break
                    print(f"  Nome vazio ou contendo '{SEP}' não é válido. Tente outro.")
                memo[seg] = new                       # mesmo segmento -> mesma sugestão
            if new != seg:
                newseg[p] = new
        return newseg

    def run(self, src_lang=None):
        ren, cfg = self.ren, self.cfg
        # None = perguntar; none/main/all. --deck-name sozinho implica escopo "all"
        mode = cfg.mode or ("all" if cfg.names else None)
        decks = ren.anki.deck_list()
        if mode == "none" or not decks:
            return
        if mode is None and not Ui.yes(
                "\nQuer também mudar os NOMES dos baralhos?\n"
                "  (Isso é só o nome das pastas onde os cartões ficam guardados no Anki. "
                "Os cartões já foram traduzidos.)", "n"):
            return
        selected = self.pick_scope(ren.paths(decks), mode)
        if not selected:
            return
        # método fixo por opção; se o escopo veio por opção, o padrão é automático
        fixed_method = {"auto": "1", "manual": "2", "review": "3", "keep": "0"}.get(
            cfg.method) or ("0" if cfg.names else "1" if mode else None)

        while True:
            method = fixed_method or Ui.choose(
                "Como você quer escolher os nomes novos?",
                {"1": (f"Automático ({ren.client.label})",
                       f"O {ren.client.label} traduz todos os nomes sozinho. Você só confere no final."),
                 "2": ("Manual",
                       "Você digita o nome novo de cada baralho. Enter mantém o nome de hoje."),
                 "3": ("Revisar um a um",
                       f"O {ren.client.label} sugere um nome para cada baralho. "
                       "Enter aceita a sugestão; ou você digita outro.")}, "1")
            plan = ren.make_plan(decks, self.build_newseg(selected, method, src_lang))
            if not plan:
                ren.rep.log("nenhum nome de baralho mudou")
                return

            print("\n=== Nomes de baralhos: como era  ->  como vai ficar ===")
            for old, new in sorted(plan.values()):
                print(f" {old}  ->  {new}")

            dups = ren.duplicates(decks, plan)
            if dups:
                print("\nProblema: mais de um baralho ficaria com o mesmo nome:")
                for k in dups:
                    print(f" {k}")
                if fixed_method or not Ui.yes("\nQuer tentar de novo, escolhendo outros nomes?", "s"):
                    return                               # método fixo daria o mesmo conflito
                continue
            if Ui.yes("\nQuer aplicar esses nomes novos?", "s"):
                break
            if not Ui.yes("\nQuer tentar de novo, escolhendo outros nomes?", "n"):
                return

        ren.apply(plan)

# ======================================================================
# Aplicação
# ======================================================================
class AnkiTranslatorApp:
    """Orquestra o fluxo: perguntas -> importar -> detectar -> traduzir -> renomear -> exportar."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes
        self.rep = ConsoleReporter(quiet=opts.quiet)
        self.cache_dir = (Path(opts.cache_dir).expanduser().resolve()
                          if opts.cache_dir else SCRIPT_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sample, self.min_ratio = opts.sample, opts.min_ratio
        self.client = choose_provider(opts)
        usage = self.client.usage()
        if usage is not None:
            self.rep.log(f"uso atual {self.client.label}:", usage)
        self.cache = TranslationCache(self.client.cache_file("cache", self.cache_dir),
                                      self.client.target_codes, self.rep)
        self.deck_cache = DeckNameCache(self.client.cache_file("deck_cache", self.cache_dir), self.rep)
        self.rep.log(f"cache: {self.cache.count()} traduções e {len(self.cache.lang)} idiomas "
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
                raise ConfigError(f"Idioma de saída inválido: {self.opts.target}. "
                                  f"Use o comando 'langs' para ver os códigos.")
        else:
            if not Ui.auto:
                Ui.show_langs("Idiomas para os quais dá para traduzir (código e nome):",
                              self.client.target_langs)
                print("Para qual idioma você quer traduzir o baralho?")
                print("Digite o CÓDIGO da lista acima (ex.: PT-BR, EN-US, ES) e aperte Enter.")
                print(f"Se você só apertar Enter, uso {self.client.default_target}.")
            while True:
                raw = Ui.ask("Idioma de saída", self.client.default_target)
                target = self.client.resolve_target(raw)
                if target:
                    break
                print(f"  '{raw}' não é um código da lista. Digite um dos códigos que aparecem acima.")
        self.target, self.tbase = target, lang_base(target)

    def show_detected(self, detected):
        print("\n=== Idioma que encontrei em cada campo ===")
        print("(Olhei alguns cartões de cada campo e adivinhei o idioma. "
              "A % diz o quanto tenho certeza.)")
        cur = None
        for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
            if mid != cur:
                print(f"\n{self.anki.model_names[mid]}")
                cur = mid
            label = lang if ratio >= self.min_ratio else f"{lang}? (incerto)"
            print(f" {f}: {label} ({ratio:.0%}) ex.: {ex}")

    def _languages_from_args(self, valid):
        raw = (self.opts.lang or "all").upper()
        if raw == "ALL":
            return set(valid)
        picked = {t for t in re.split(r"[,\s;]+", raw) if t}
        bad = sorted(picked - set(valid))
        if bad:
            raise ConfigError(f"Idioma(s) não detectado(s) nos campos: {', '.join(bad)}. "
                              f"Detectados: {', '.join(valid)}")
        return picked

    def ask_languages(self, detected):
        counts = collections.Counter(
            lang for (lang, ratio, _) in detected.values()
            if ratio >= self.min_ratio and lang_base(lang) != self.tbase)
        already = sum(1 for (lang, ratio, _) in detected.values()
                      if ratio >= self.min_ratio and lang_base(lang) == self.tbase)
        options = counts.most_common()
        if not options:
            raise AnkiToolError("\nNenhum campo em outro idioma para traduzir.")

        labels = [f"{code} - {self.client.source_names.get(code, code)} ({c} campos)"
                  for code, c in options]
        note = (f"({already} campo(s) já estão em {self.tbase} e serão mantidos do jeito que estão; "
                f"campos 'incertos' também.)") if already else None

        if self.opts.lang or Ui.auto:                    # sem pergunta: --lang, ou todos com --yes
            print(f"\n=== Idiomas que podem ser traduzidos para {self.target} ===")
            for i, lab in enumerate(labels, 1):
                print(f" {i}) {lab}")
            if note:
                print(f" {note}")
            return self._languages_from_args(dict(options))

        intro = ("Estes são os idiomas que encontrei no baralho. Escolha quais você quer traduzir."
                 + (f"\n{note}" if note else ""))
        idx = Ui.select(f"Quais idiomas você quer traduzir para {self.target}?", labels,
                        multi=True, default="all" if len(options) == 1 else None,
                        intro=intro,
                        hint="Também vale digitar o código do idioma (ex.: IT, ou IT,EN).",
                        aliases={code.upper(): i for i, (code, _) in enumerate(options)})
        if idx is None:
            raise Cancelled("Cancelado.")
        return {options[i][0] for i in idx}

    # ---- fluxo ----
    def run(self):
        if self.opts.dry_run:                            # simulação: não gera arquivo
            src, out = ask_input(self.opts), None
        else:
            src, out = self.ask_paths()
        self.ask_target()

        self.anki = AnkiCollection(src, self.opts.keep_scheduling, self.opts.legacy, self.rep)
        try:
            self.anki.import_package()
            self.anki.load_notes()

            if self.opts.only_fields or self.opts.skip_fields:
                before = len(self.anki.field_values)
                for nt, fld in self.anki.filter_fields(self.opts.only_fields, self.opts.skip_fields):
                    self.rep.warn(f"--field '{(nt + ':') if nt else ''}{fld}' não corresponde a nenhum campo")
                self.rep.log(f"campos considerados: {len(self.anki.field_values)} de {before} "
                             f"(por --field / --skip-field)")
                if not self.anki.field_values:
                    raise ConfigError("Nenhum campo sobrou depois de --field/--skip-field. "
                                      "Use o comando 'info' para ver os nomes dos campos.")

            detector = FieldLanguageDetector(self.client, self.cache, self.target, self.rep,
                                             sample=self.sample)
            detected = detector.detect(self.anki.field_values)
            self.show_detected(detected)

            picked = self.ask_languages(detected)
            chosen = {k: (lang, self.target) for k, (lang, ratio, _) in detected.items()
                      if lang in picked and ratio >= self.min_ratio}
            self.rep.log("idiomas escolhidos:", ", ".join(sorted(picked)), f"-> {len(chosen)} campos")

            dry = self.opts.dry_run
            FieldTranslator(self.client, self.cache, self.rep).translate(
                chosen, self.anki.field_values, dry_run=dry)
            if dry:
                usage = self.client.usage()
                if usage is not None:
                    print(f"Uso atual do {self.client.label}: {usage}")
                print("(A detecção de idioma já gastou alguns caracteres.)")
                print("Nada foi traduzido nem exportado (--dry-run).")
                return
            self.anki.apply_translations(chosen, self.cache)

            src_lang = next(iter(picked)) if len(picked) == 1 else None
            renamer = DeckRenamer(self.anki, self.client, self.target, self.deck_cache, self.rep)
            DeckRenameCli(renamer, RenameConfig(self.opts.rename_decks, self.opts.deck_method,
                                                self.opts.deck_names)).run(src_lang)

            self.anki.export(out)
            print("pronto ->", out)
            usage = self.client.usage()
            if usage is not None:
                self.rep.log(f"uso {self.client.label} ao final:", usage)
        finally:
            self.anki.close()
# ======================================================================
# Inverter cartões (comando separado, sem serviço de tradução)
# ======================================================================
def ask_input(opts):
    """Lê o .apkg de entrada das opções ou abre o seletor de arquivos."""
    if opts.input:
        src = Path(opts.input).expanduser().resolve()
    elif Ui.auto:
        raise ConfigError("Com -y/--yes é preciso informar o arquivo .apkg de entrada.")
    else:
        print("\nPrimeiro, escolha o baralho (arquivo .apkg) que você quer usar.")
        src = pick_file(default_start())
        if src is None:
            raise Cancelled("Cancelado.")
    if not src.exists():
        raise ConfigError(f"Arquivo não encontrado: {src}")
    return src

def ask_paths(opts, suffix="_out"):
    """Escolhe o .apkg de entrada (seletor) e o nome do .apkg de saída."""
    src = ask_input(opts)
    if opts.output:
        out = Path(opts.output).expanduser()
    else:
        default = f"{src.stem}{suffix}.apkg"
        if Ui.auto:
            name = default
        else:
            print("\nAgora escolha o nome do arquivo NOVO que vou criar. O arquivo original não é alterado.")
            print(f"Ele será salvo na mesma pasta do original: {src.parent}")
            name = Ui.ask("Nome do arquivo novo (só Enter = usar o nome sugerido)", default)
        out = Path(name).expanduser()
    if not out.is_absolute() and out.parent == Path("."):
        out = src.parent / out                            # nome simples: fica ao lado do original
    out = out.resolve()
    if out.suffix.lower() != ".apkg":
        out = out.with_name(out.name + ".apkg")
    if out == src:
        raise ConfigError("O arquivo novo não pode ter o mesmo nome do original.")
    if out.exists() and not opts.force:
        if Ui.auto:
            raise ConfigError(f"O arquivo '{out}' já existe. Use --force para substituí-lo "
                              f"ou escolha outro nome com -o.")
        if not Ui.yes(f"\nJá existe um arquivo chamado '{out.name}'. Quer substituí-lo?", "n"):
            raise Cancelled("Cancelado: rode de novo e escolha outro nome.")
    return src, out

class CardReverser:
    """Inverte frente e verso trocando, nos templates dos cartões, as referências a PARES de
    campos ({{Palavra}} <-> {{Tradução}}). Vários pares são trocados de uma vez, o que é
    necessário quando cada lado tem campos de áudio, frase etc. O conteúdo das notas não
    muda e a operação é reversível: rodar de novo desfaz. Só entram tipos de nota padrão com
    2+ campos (o tipo cloze, que tem um campo único, fica de fora)."""

    REF = re.compile(r"\{\{([#^/]?)((?:[^{}:]*:)*)([^{}:]+)\}\}")
    STANDARD = 0                                         # no Anki, cloze é 1

    def __init__(self, anki, rep=None):
        self.anki, self.rep = anki, rep or Reporter()
        self.counts = {}                                 # id do tipo de nota -> nº de notas

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
                continue                                 # tipos padrão do Anki sem notas
            self.counts[nt["id"]] = n
            if nt["type"] == self.STANDARD and len(nt["flds"]) >= 2:
                out.append(nt)
            else:
                skipped += 1
        if skipped:
            self.rep.log(f"tipos de nota ignorados (cloze ou com menos de 2 campos): {skipped}")
        return sorted(out, key=lambda nt: nt["name"].lower())

    def select_by_name(self, cands, names):
        wanted = {n.lower(): n for n in names}
        chosen = [nt for nt in cands if nt["name"].lower() in wanted]
        missing = set(wanted) - {nt["name"].lower() for nt in chosen}
        if missing:
            raise ConfigError(
                f"Tipo(s) de nota não encontrado(s): {', '.join(wanted[m] for m in sorted(missing))}. "
                f"Disponíveis: {', '.join(nt['name'] for nt in cands)}")
        return chosen

    def pairs_for(self, nt, swap=None):
        """Pares a trocar: os de 'swap' (["A=B"]) ou os sugeridos. None se 'swap' não serve."""
        if swap:
            names = [f["name"] for f in nt["flds"]]
            pairs = self.parse_pairs(",".join(swap), names)
            if not pairs:
                self.rep.warn(f" {nt['name']}: não tem os campos de --swap; ignorado")
            return pairs
        return self.suggest_pairs(nt)

    def build_plan(self, cfg):
        """Plano sem perguntas (uso como biblioteca / --yes): [(tipo_de_nota, pares)]."""
        cands = self.eligible()
        chosen = self.select_by_name(cands, cfg.notetypes) if cfg.notetypes else cands
        plan = []
        for nt in chosen:
            pairs = self.pairs_for(nt, cfg.swap)
            if pairs:
                plan.append((nt, pairs))
        return plan

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
                self.rep.log(f" {nt['name']}: nenhum template usa os campos escolhidos; ignorado")
                continue
            save(nt)
            done += 1
        self.rep.log(f"tipos de nota invertidos: {done}/{len(plan)}")
        return done

class ReverseCli:
    """Perguntas e textos do comando 'reverse'. Toda a lógica está em CardReverser."""

    def __init__(self, rev, cfg):
        self.rev, self.cfg = rev, cfg

    def pick_notetypes(self, cands):
        if self.cfg.notetypes:
            return self.rev.select_by_name(cands, self.cfg.notetypes)
        if Ui.auto:
            return cands
        labels = [f"{nt['name']}  ({self.rev.counts[nt['id']]} notas, {len(nt['flds'])} campos)"
                  for nt in cands]
        idx = Ui.select(
            "Quais tipos de nota você quer inverter?", labels, multi=True, default="all",
            intro=("Um 'tipo de nota' é o modelo dos seus cartões (por exemplo: Básico, Vocabulário).\n"
                   "Cada tipo tem 'campos', como Palavra e Tradução. Os números entre parênteses\n"
                   "dizem quantas notas usam esse tipo."))
        return [] if idx is None else [cands[i] for i in idx]

    def show_layout(self, nt, pairs):
        front, back = CardReverser.layout(nt)
        paired = {n for p in pairs for n in p}
        stay = [f["name"] for f in nt["flds"] if f["name"] not in paired]
        print(f"\n Tipo de nota: {nt['name']} ({self.rev.counts[nt['id']]} notas)")
        print(f"   Hoje a FRENTE do cartão mostra: {', '.join(front) or '-'}")
        print(f"   Hoje o VERSO do cartão mostra:  {', '.join(back) or '-'}")
        print("   Para inverter, eu trocaria de lugar estes campos:")
        for a, b in pairs:
            print(f"     {a}  <->  {b}")
        if stay:
            print(f"   Estes não mudam de lado: {', '.join(stay)}")

    def pick_pairs(self, nt):
        names = [f["name"] for f in nt["flds"]]
        if self.cfg.swap:
            return self.rev.pairs_for(nt, self.cfg.swap)
        pairs = self.rev.suggest_pairs(nt)
        if Ui.auto or len(names) == 2:                    # com 2 campos não há o que escolher
            return pairs
        self.show_layout(nt, pairs)
        key = Ui.choose(
            f"O que fazer com o tipo '{nt['name']}'?",
            {"1": ("Usar a troca sugerida acima", "É o que eu recomendo na maioria dos casos."),
             "2": ("Digitar outros pares de campos",
                   "Você diz quais campos trocam de lugar."),
             "0": ("Pular este tipo de nota", "Ele não será invertido.")}, "1")
        if key == "1":
            return pairs
        if key == "0":
            return None
        print("\nDigite os pares assim: Campo1=Campo2 (para vários, separe por vírgula).")
        print(f"Exemplo: {names[0]}={names[1]}")
        print(f"Campos deste tipo: {', '.join(names)}")
        while True:
            raw = input("Pares (ou 0 para pular este tipo): ").strip()
            if raw == "0":
                return None
            custom = CardReverser.parse_pairs(raw, names)
            if custom:
                return custom
            print(f"  Pares inválidos. Use só nomes de campos deste tipo: {', '.join(names)}")

    def run(self):
        """Devolve quantos tipos de nota foram invertidos."""
        cands = self.rev.eligible()
        if not cands:
            self.rev.rep.log("nenhum tipo de nota com frente e verso para inverter")
            return 0
        plan = []
        for nt in self.pick_notetypes(cands):
            pairs = self.pick_pairs(nt)
            if pairs:
                plan.append((nt, pairs))
        if not plan:
            return 0
        print("\n=== Resumo: o que vai ser invertido ===")
        for nt, pairs in plan:
            print(f" {nt['name']} ({self.rev.counts[nt['id']]} notas, "
                  f"{len(nt['tmpls'])} modelo(s) de cartão):")
            for a, b in pairs:
                print(f"   {a}  <->  {b}")
        if not Ui.yes("\nQuer inverter esses cartões?", "s"):
            return 0
        return self.rev.apply(plan)

class ReverseApp:
    """Comando reverse: importar -> inverter cartões -> exportar. Não usa serviço de tradução."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes

    def run(self):
        if not Ui.auto:
            print("\n=== Inverter os cartões ===")
            print("O que hoje aparece na FRENTE do cartão passa para o VERSO, e vice-versa.")
            print("O texto das suas notas não muda: só muda o jeito de mostrar.")
            print("Se rodar de novo no arquivo novo, tudo volta ao que era.")
        src, out = ask_paths(self.opts, suffix="_reversed")
        rep = ConsoleReporter(quiet=self.opts.quiet)
        anki = AnkiCollection(src, self.opts.keep_scheduling, self.opts.legacy, rep)
        try:
            anki.import_package()
            if not ReverseCli(CardReverser(anki, rep),
                             ReverseConfig(self.opts.notetype, self.opts.swap)).run():
                raise AnkiToolError("Nada foi invertido; nenhum arquivo gerado.")
            anki.export(out)
            print("pronto ->", out)
        finally:
            anki.close()

class InfoApp:
    """Comando info: mostra tipos de nota, campos e baralhos do .apkg (não gera arquivo)."""

    def __init__(self, opts):
        self.opts = opts

    def run(self):
        anki = AnkiCollection(ask_input(self.opts), rep=ConsoleReporter(quiet=self.opts.quiet))
        try:
            anki.import_package()
            print("\n=== Tipos de nota (os modelos dos seus cartões) ===")
            for nt in sorted(anki.notetypes(), key=lambda nt: nt["name"].lower()):
                n = anki.note_count(nt["id"])
                if n == 0:
                    continue                             # tipos padrão do Anki sem notas
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
        finally:
            anki.close()
# ======================================================================
# Linha de comando
# ======================================================================
COMMANDS = ("translate", "reverse", "info", "langs")
MENU = {"1": ("translate", "Traduzir um baralho",
              "Troca o idioma do texto dos cartões (ex.: de italiano para português). Usa o DeepL."),
        "2": ("reverse", "Inverter os cartões (frente <-> verso)",
              "O que aparece na frente passa para o verso, e vice-versa. O texto não muda."),
        "3": ("info", "Ver o que tem dentro de um baralho",
              "Só mostra informações (tipos de nota, campos, baralhos). Não cria nenhum arquivo."),
        "0": ("exit", "Sair", None)}
BACK_TO_MENU = "\nQuer fazer mais alguma coisa?\n  (s = voltar ao menu principal, n = fechar o programa)"

def positive_int(s):
    try:
        v = int(s)
    except ValueError:
        v = 0
    if v < 1:
        raise argparse.ArgumentTypeError(f"'{s}' não é um número inteiro maior que zero")
    return v

def ratio_type(s):
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        v = 0.0
    if not 0 < v <= 1:
        raise argparse.ArgumentTypeError(f"'{s}' não está entre 0 e 1 (ex.: 0.6)")
    return v

def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "-V", "--version"):
        argv.insert(0, "translate")                      # atalho: script deck.apkg -t PT-BR

    p = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Ferramentas para baralhos .apkg do Anki. Sem nenhum argumento, abre o "
                    "modo interativo (menu). Use 'COMANDO -h' para ver as opções de cada comando.",
        epilog="Em qualquer comando, o que não for informado por opção é perguntado, "
               "a menos que -y/--yes seja usado.")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMANDO")

    io = argparse.ArgumentParser(add_help=False)
    io.add_argument("input", nargs="?", metavar="ENTRADA", help="arquivo .apkg de entrada")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-y", "--yes", action="store_true",
                        help="não perguntar nada: usa os padrões de cada comando")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="só mostra avisos e o resultado final (sem mensagens de andamento)")
    run_opts = argparse.ArgumentParser(add_help=False, parents=[common])
    run_opts.add_argument("-o", "--output", metavar="SAIDA", help="arquivo .apkg de saída")
    run_opts.add_argument("-f", "--force", action="store_true",
                          help="substitui o arquivo de saída se ele já existir "
                               "(sem isto, com -y o programa para com erro)")
    run_opts.add_argument("--keep-scheduling", action="store_true",
                          help="mantém o histórico de estudo (datas, intervalos) e as opções "
                               "dos baralhos no arquivo gerado (padrão: removidos)")
    run_opts.add_argument("--legacy", action=argparse.BooleanOptionalAction, default=True,
                          help="formato antigo do .apkg, aceito por qualquer versão do Anki/"
                               "AnkiDroid (padrão); --no-legacy gera o formato novo, menor")

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
    t.add_argument("--dry-run", action="store_true",
                   help="só calcula quantos textos e caracteres seriam traduzidos: não traduz "
                        "nem gera arquivo (a detecção de idioma ainda gasta alguns caracteres)")
    t.add_argument("--field", action="append", dest="only_fields", metavar="CAMPO",
                   help="traduz SÓ este campo (ou TIPO_DE_NOTA:CAMPO); pode repetir")
    t.add_argument("--skip-field", action="append", dest="skip_fields", metavar="CAMPO",
                   help="NÃO traduz este campo (ou TIPO_DE_NOTA:CAMPO); pode repetir")
    t.add_argument("--sample", type=positive_int, default=SAMPLE, metavar="N",
                   help=f"quantos textos de cada campo usar para detectar o idioma (padrão: {SAMPLE})")
    t.add_argument("--min-ratio", type=ratio_type, default=MIN_RATIO, metavar="R",
                   help=f"fração mínima (0 a 1) da amostra que precisa concordar no idioma "
                        f"para o campo ser considerado seguro (padrão: {MIN_RATIO})")
    t.add_argument("--cache-dir", metavar="PASTA",
                   help="pasta onde ficam os arquivos de cache (padrão: a pasta do script)")

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

    sub.add_parser("info", parents=[io, common],
                   help="mostra tipos de nota, campos e baralhos de um .apkg",
                   description="Mostra tipos de nota, campos e baralhos de um .apkg "
                               "(útil para escolher --notetype e --swap). Não gera arquivo.")
    lg = sub.add_parser("langs", parents=[common],
                        help="lista os idiomas do serviço de tradução (saída ou origem)")
    lg.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICO",
                    help=f"serviço de tradução (padrão: {DEFAULT_PROVIDER})")
    lg.add_argument("--source", action="store_true",
                    help="lista os idiomas de ORIGEM (os que o serviço reconhece) "
                         "em vez dos de saída")

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
        for attr, flag in (("only_fields", "--field"), ("skip_fields", "--skip-field")):
            specs = []
            for item in getattr(opts, attr) or []:
                nt, _, fld = item.rpartition(":")
                if not fld.strip():
                    t.error(f"{flag} inválido: '{item}'. Use CAMPO ou TIPO_DE_NOTA:CAMPO")
                specs.append((nt.strip() or None, fld.strip()))
            setattr(opts, attr, specs)
    elif opts.command == "reverse":
        seen = set()
        for item in opts.swap or []:
            parts = [x.strip().lower() for x in item.split("=")]
            if len(parts) != 2 or not all(parts) or parts[0] == parts[1] or seen & set(parts):
                r.error(f"--swap inválido: '{item}'. Use CAMPO1=CAMPO2, sem repetir campos")
            seen.update(parts)
    return opts

def _failed(text, from_menu):
    """Mostra o erro. Devolve True se deve voltar ao menu; fora do menu, encerra com código 1."""
    print(f"\n{text}", file=sys.stdout if from_menu else sys.stderr)
    if not from_menu:
        sys.exit(1)
    return not Ui.auto and Ui.yes(BACK_TO_MENU, "s")

def main(argv=None):
    from_menu = False                                    # só volta ao menu quem começou por ele
    while True:
        try:
            opts = parse_args(argv)
            if getattr(opts, "command", None) is None:
                from_menu = True
                options = {k: (v[1], v[2]) for k, v in MENU.items()}
                key = Ui.choose("O que você quer fazer?", options, "1")
                if key == "0":
                    break
                opts = parse_args([MENU[key][0]])

            Ui.auto = getattr(opts, "yes", False)

            if opts.command == "reverse":
                ReverseApp(opts).run()
            elif opts.command == "info":
                InfoApp(opts).run()
            elif opts.command == "langs":
                provider = choose_provider(opts)
                if opts.source:
                    Ui.show_langs("Idiomas de origem que o serviço reconhece (código e nome):",
                                  provider.source_langs)
                else:
                    Ui.show_langs("Idiomas de saída disponíveis (código e nome):",
                                  provider.target_langs)
            elif opts.command != "exit":
                AnkiTranslatorApp(opts).run()

            if not from_menu or Ui.auto or not Ui.yes(BACK_TO_MENU, "s"):
                break
            argv = []  # Reseta os argumentos para forçar o menu interativo na próxima iteração

        except AnkiToolError as e:                       # erros esperados (nossos)
            if not _failed(f"[Aviso] {e}", from_menu):
                break
            argv = []
        except SystemExit:                               # só vem do argparse: -h, -V, erro de uso
            if not from_menu:
                raise
            if Ui.auto or not Ui.yes(BACK_TO_MENU, "s"):
                break
            argv = []
        except KeyboardInterrupt:
            print("\nInterrompido.")
            sys.exit(130)
        except Exception as e:
            if not _failed(f"[Erro Inesperado] {e}", from_menu):
                break
            argv = []

if __name__ == "__main__":
    main()