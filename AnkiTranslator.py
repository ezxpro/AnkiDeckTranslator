# pip install anki deepl

import json, os, re, sys, time, random, tempfile, collections, hashlib, threading, deepl
from pathlib import Path
from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ImportAnkiPackageOptions, ExportAnkiPackageOptions)

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH, SAMPLE, MIN_RATIO = 40, 10, 0.6
SEP = "::"


# ======================================================================
# Utilitários de console
# ======================================================================
class Ui:
    """Entrada/saída de console: log, perguntas, menus e barra de progresso."""

    @staticmethod
    def log(*a):
        print(time.strftime("%H:%M:%S"), *a, flush=True)

    @staticmethod
    def ask(msg, default):
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
# DeepL
# ======================================================================
class DeepLClient:
    """Encapsula a chave, o tradutor e as chamadas com retentativa."""

    def __init__(self, key=None):
        self.key = key or self.load_key()
        self.tr = deepl.Translator(self.key)
        self.target_langs = self.tr.get_target_languages()
        self.target_codes = {l.code for l in self.target_langs}
        self.source_names = {l.code: l.name for l in self.tr.get_source_languages()}

    @staticmethod
    def load_key():
        """Procura DEEPL_KEY na variável de ambiente; se não achar, lê o arquivo .env
        que fica na mesma pasta do script (linha no formato DEEPL_KEY=sua-chave:fx)."""
        key = os.environ.get("DEEPL_KEY", "").strip()
        if not key:
            env_file = SCRIPT_DIR / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, value = line.split("=", 1)
                    if name.replace("export", "").strip() == "DEEPL_KEY":
                        key = value.strip().strip('"').strip("'")
                        break
        if not key:
            raise SystemExit("Chave do DeepL não encontrada. Defina a variável de ambiente "
                             "DEEPL_KEY ou crie um arquivo .env ao lado do script com:\n"
                             "DEEPL_KEY=sua-chave-aqui:fx")
        return key

    def usage(self):
        return self.tr.get_usage().character

    def translate(self, texts, retries=3, **kwargs):
        """Traduz com até 'retries' tentativas; devolve None se o DeepL não responder."""
        for _ in range(retries):
            try:
                return self.tr.translate_text(texts, **kwargs)
            except deepl.ConnectionException:
                time.sleep(3)
        return None


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
    """deck_cache.json, separado do cache.json. Só a saída bruta do DeepL é gravada;
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
        self.src = src
        self.col = None
        self.notes = []
        self.model_names = {}                                  # mid -> nome do modelo
        self.field_values = collections.defaultdict(list)      # (mid, campo) -> valores com texto

    def import_package(self):
        log(f"importando {self.src} ({os.path.getsize(self.src) / 1e6:.1f} MB)")
        self.col = Collection(os.path.join(tempfile.mkdtemp(), "collection.anki2"))
        with Heartbeat("importando baralho"):
            self.col.import_anki_package(ImportAnkiPackageRequest(
                package_path=self.src, options=ImportAnkiPackageOptions()))

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

    def export(self, out_path):
        with Heartbeat("exportando"):
            self.col.export_anki_package(out_path=out_path, options=ExportAnkiPackageOptions(
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
                res = self.client.tr.translate_text(fresh, target_lang=self.target)
                for p, r in zip(fresh, res):
                    lang_cache[h(p)] = r.detected_source_lang
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
        chars = sum(len(TextTools.mask(v)) for (_, _, v) in queued)
        log(f"a traduzir: {len(queued)} textos (~{chars} caracteres); "
            f"já estavam no cache (sem custo): {len(cached)}")

        for (L, T), texts in todo.items():
            texts = sorted(texts)
            for i in range(0, len(texts), BATCH):
                chunk = texts[i:i + BATCH]
                res = self.client.translate([TextTools.mask(t) for t in chunk],
                                            source_lang=L, target_lang=T, tag_handling="html")
                if res is None:
                    raise SystemExit("DeepL fora do ar; rode de novo (o cache preserva o progresso).")
                for src, r in zip(chunk, res):
                    out = TextTools.unmask(r.text)
                    ok = TextTools.PROT.findall(out) == TextTools.PROT.findall(src)
                    if not ok:
                        log(" tag danificada, mantido original:", TextTools.plain(src)[:50])
                    self.cache.put(T, L, src, out if ok else src)
                self.cache.save()
                log(f" [{L}->{T}] {min(i + BATCH, len(texts))}/{len(texts)}")


# ======================================================================
# Renomear baralhos
# ======================================================================
class DeckRenamer:
    """Traduz/altera nomes de baralhos. Traduz segmentos soltos (nunca o caminho completo).
    Baralhos principais são os de primeiro nível; baralhos secundários são os que
    ficam dentro de outro baralho (separados por '::')."""

    def __init__(self, anki, client, target, picked, cache):
        self.anki, self.client = anki, client
        self.target, self.tbase = target, target.split("-")[0]
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
        scope = Ui.choose("Quais baralhos traduzir?",
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
        for i in range(0, len(pending), BATCH):
            chunk = pending[i:i + BATCH]
            res = self.client.translate(chunk, source_lang=src_lang, target_lang=self.target)
            if res is None:
                log("DeepL fora do ar; nomes restantes ficam como estão")
                break
            for s, r in zip(chunk, res):
                already = r.detected_source_lang == self.tbase    # já está no idioma de saída
                bucket[s] = s if already else self.clean_seg(r.text, s)
            self.cache.save()
        return {s: bucket.get(s, s) for s in segments}

    def build_newseg(self, selected, method, src_lang):
        ordered = sorted(selected, key=lambda p: tuple(s.lower() for s in p))
        auto = {}
        if method in ("1", "3"):
            auto = self.auto_names({p[-1] for p in selected}, src_lang)
        newseg, memo = {}, {}
        for p in ordered:
            seg = p[-1]
            if method == "1":
                new = auto.get(seg, seg)
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
        decks = self.anki.deck_list()
        if not decks or not Ui.yes("\nTraduzir/alterar nomes de baralhos?", "n"):
            return
        selected = self.pick_scope(decks)
        if not selected:
            return
        src_lang = next(iter(self.picked)) if len(self.picked) == 1 else None

        while True:
            method = Ui.choose("Como alterar os nomes?",
                               {"1": "Automático (DeepL)",
                                "2": "Manual (você digita; Enter mantém o atual)",
                                "3": "Revisar um a um (sugestão do DeepL; Enter aceita)"}, "1")
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
                if not Ui.yes("Tentar de novo?", "s"):
                    return
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

    def __init__(self):
        self.client = DeepLClient()
        log("uso atual DeepL:", self.client.usage())
        self.cache = TranslationCache(SCRIPT_DIR / "cache.json", self.client.target_codes)
        self.deck_cache = DeckNameCache(SCRIPT_DIR / "deck_cache.json")
        log(f"cache: {self.cache.count()} traduções e {len(self.cache.lang)} idiomas "
            f"de texto guardados em {self.cache.path}")
        self.anki = None
        self.target = self.tbase = None

    # ---- perguntas ----
    def ask_paths(self):
        src = os.path.abspath(Ui.ask("Arquivo de entrada", "italian.apkg"))
        if not os.path.exists(src):
            raise SystemExit(f"Arquivo não encontrado: {src}")
        base = os.path.splitext(os.path.basename(src))[0]
        out = os.path.abspath(Ui.ask("Arquivo de saída", f"{base}_out.apkg"))
        if not out.lower().endswith(".apkg"):
            out += ".apkg"
        return src, out

    def ask_target(self):
        Ui.show_langs("Idiomas de saída disponíveis (código nome):", self.client.target_langs)
        while True:
            target = Ui.ask("Idioma de saída (Enter = português do Brasil, "
                            "ou digite um código da lista acima)", "PT-BR").upper()
            if target in self.client.target_codes:
                break
            print(f" '{target}' não é um código válido. Use um dos códigos da lista acima.")
        self.target, self.tbase = target, target.split("-")[0]

    def show_detected(self, detected):
        print("\n=== Idioma detectado em cada campo ===")
        cur = None
        for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
            if mid != cur:
                print(f"\n{self.anki.model_names[mid]}")
                cur = mid
            label = lang if ratio >= MIN_RATIO else f"{lang}? (incerto)"
            print(f" {f}: {label} ({ratio:.0%}) ex.: {ex}")

    def ask_languages(self, detected):
        counts = collections.Counter(
            lang for (lang, ratio, _) in detected.values()
            if ratio >= MIN_RATIO and lang != self.tbase)
        already = sum(1 for (lang, ratio, _) in detected.values()
                      if ratio >= MIN_RATIO and lang == self.tbase)
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

        DeckRenamer(self.anki, self.client, self.target, picked, self.deck_cache).run()

        self.anki.export(out)
        self.anki.close()
        log("pronto ->", out)
        log("uso DeepL ao final:", self.client.usage())


if __name__ == "__main__":
    AnkiTranslatorApp().run()
