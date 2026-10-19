# pip install anki deepl
import json, os, re, time, random, tempfile, collections, deepl
from pathlib import Path
from anki.collection import (Collection, ImportAnkiPackageRequest,
    ImportAnkiPackageOptions, ExportAnkiPackageOptions)

def load_key():
    """Procura DEEPL_KEY na variável de ambiente; se não achar, lê o arquivo .env
    que fica na mesma pasta do script (linha no formato DEEPL_KEY=sua-chave:fx)."""
    key = os.environ.get("DEEPL_KEY", "").strip()
    if not key:
        env_file = Path(__file__).resolve().parent / ".env"
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

KEY = load_key()
BATCH, SAMPLE, MIN_RATIO = 40, 10, 0.6
CACHE_FILE = Path(__file__).resolve().parent / "cache.json"   # sempre ao lado do script

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def ask(msg, default):
    return input(f"{msg} [{default}]: ").strip().strip('"') or default

def show_langs(title, langs):
    items = sorted(langs, key=lambda l: l.name)
    half = (len(items) + 1) // 2
    print(f"\n{title}")
    for i in range(half):
        left = f"  {items[i].code:<8}{items[i].name}"
        right = ""
        if i + half < len(items):
            r = items[i + half]
            right = f"{r.code:<8}{r.name}"
        print(f"{left:<40}{right}")
    print()

# ---- DeepL ----
tr = deepl.Translator(KEY)
log("uso atual DeepL:", tr.get_usage().character)
tlangs = tr.get_target_languages()
tcodes = {l.code for l in tlangs}
src_names = {l.code: l.name for l in tr.get_source_languages()}

# ---- cache: traduções (por idioma de saída + texto) e idiomas detectados (por arquivo) ----
def load_cache():
    data = {}
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bak = CACHE_FILE.with_name(CACHE_FILE.name + ".bak")
            CACHE_FILE.replace(bak)
            log(f"cache.json estava ilegível; guardei uma cópia em {bak.name} e comecei um novo")
            data = {}
    if data and data.get("version") != 2:   # formato antigo: chaves "SAIDA|ORIGEM|texto"
        old, data, migrated = data, {}, 0
        data["trans"] = {}
        for k, v in old.items():
            parts = k.split("|", 2)
            if len(parts) == 3 and parts[0] in tcodes:
                data["trans"].setdefault(parts[0], {})[parts[2]] = v
                migrated += 1
        log(f"cache antigo convertido: {migrated} traduções aproveitadas")
    data["version"] = 2
    data.setdefault("trans", {})
    data.setdefault("detect", {})
    return data

def save_cache():
    tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")   # grava num temporário e troca:
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)                                # fechar o programa não corrompe

cache = load_cache()
log(f"cache: {sum(len(t) for t in cache['trans'].values())} traduções guardadas em {CACHE_FILE}")

# ---- perguntas ----
SRC = os.path.abspath(ask("Arquivo de entrada", "italian.apkg"))
if not os.path.exists(SRC):
    raise SystemExit(f"Arquivo não encontrado: {SRC}")
base = os.path.splitext(os.path.basename(SRC))[0]
OUT = os.path.abspath(ask("Arquivo de saída", f"{base}_out.apkg"))
if not OUT.lower().endswith(".apkg"):
    OUT += ".apkg"

show_langs("Idiomas de saída disponíveis (código  nome):", tlangs)
while True:
    TARGET = ask("Idioma de saída (Enter = português do Brasil, ou digite um código da lista acima)",
                 "PT-BR").upper()
    if TARGET in tcodes:
        break
    print(f"  '{TARGET}' não é um código válido. Use um dos códigos da lista acima.")
TBASE = TARGET.split("-")[0]

# ---- proteção de tags ----
PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)
def mask(t):   return PROT.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', t)
def unmask(t): return WRAP.sub(r"\1", t)
def plain(s):  return re.sub(r"\[sound:[^\]]+\]|<[^>]+>|&\w+;|\{\{c\d+::|\}\}", "", s).strip()

# ---- importa o baralho ----
log("importando", SRC)
col = Collection(os.path.join(tempfile.mkdtemp(), "collection.anki2"))
col.import_anki_package(ImportAnkiPackageRequest(
    package_path=SRC, options=ImportAnkiPackageOptions()))
notes = [col.get_note(n) for n in col.find_notes("")]
log("notas:", len(notes))

names = {}
vals = collections.defaultdict(list)   # (mid, campo) -> valores com texto
for n in notes:
    if n.mid not in names:
        names[n.mid] = n.note_type()["name"]
    for f, v in n.items():
        if len(plain(v)) >= 3:
            vals[(n.mid, f)].append(v)

# ---- 1) detecta o idioma de cada campo ----
fp = f"{os.path.basename(SRC)}|{os.path.getsize(SRC)}|{int(os.path.getmtime(SRC))}"   # identifica o arquivo
det_cache = cache["detect"].setdefault(fp, {})
detected = {}   # (mid, campo) -> (idioma, proporção, exemplo)
novos = 0
for (mid, f), vs in vals.items():
    key = f"{names[mid]}|{f}"
    if key not in det_cache:
        if novos == 0:
            log(f"detectando o idioma dos campos (amostra de até {SAMPLE} textos cada)...")
        sample = random.sample(vs, min(SAMPLE, len(vs)))
        res = tr.translate_text([plain(s) for s in sample], target_lang=TARGET)
        lang, cnt = collections.Counter(r.detected_source_lang for r in res).most_common(1)[0]
        ex = re.sub(r"\s+", " ", plain(sample[0]))[:45]
        det_cache[key] = [lang, cnt / len(res), ex]
        novos += 1
    detected[(mid, f)] = tuple(det_cache[key])
if novos:
    save_cache()
else:
    log("idiomas já detectados antes (cache): nenhum caractere gasto na detecção")

# ---- 2) mostra tudo ----
print("\n=== Idioma detectado em cada campo ===")
cur = None
for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
    if mid != cur:
        print(f"\n{names[mid]}")
        cur = mid
    label = lang if ratio >= MIN_RATIO else f"{lang}? (incerto)"
    print(f"  {f}: {label} ({ratio:.0%})   ex.: {ex}")

counts = collections.Counter(
    lang for (lang, ratio, _) in detected.values() if ratio >= MIN_RATIO and lang != TBASE)
ja_no_alvo = sum(1 for (lang, ratio, _) in detected.values()
                 if ratio >= MIN_RATIO and lang == TBASE)
options = counts.most_common()
if not options:
    col.close()
    raise SystemExit("\nNenhum campo em outro idioma para traduzir.")

print(f"\n=== Idiomas que podem ser traduzidos para {TARGET} ===")
for i, (code, c) in enumerate(options, 1):
    print(f"  {i}) {code} - {src_names.get(code, code)} ({c} campos)")
if ja_no_alvo:
    print(f"  ({ja_no_alvo} campos já estão em {TBASE} e serão mantidos; campos 'incertos' também)")

# ---- 3) escolhe qual(is) idioma(s) traduzir ----
valid = dict(options)
default = "1" if len(options) == 1 else ""
while True:
    prompt = "\nTraduzir qual idioma? número ou código (vários separados por vírgula, n = cancelar)"
    raw = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip().strip('"').upper() or default
    if raw in ("N", "NAO", "NÃO"):
        col.close()
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
        break
    print(f"  Opção inválida: {', '.join(bad) or raw}. Escolha entre os números/códigos listados.")

chosen = {k: (lang, TARGET) for k, (lang, ratio, _) in detected.items()
          if lang in picked and ratio >= MIN_RATIO}
log("idiomas escolhidos:", ", ".join(sorted(picked)), f"-> {len(chosen)} campos")

# ---- traduz (cache por idioma de saída + texto) ----
trans = cache["trans"]
todo = collections.defaultdict(list)   # (origem, saída) -> textos
queued, hits = set(), 0
for (mid, f), (L, T) in chosen.items():
    tc = trans.get(T, {})
    for v in vals[(mid, f)]:
        if v in tc:
            hits += 1
        elif (T, v) not in queued:
            queued.add((T, v))
            todo[(L, T)].append(v)
log(f"a traduzir: {len(queued)} (já estavam no cache: {hits})")

for (L, T), texts in todo.items():
    texts = sorted(texts)
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        for _ in range(3):
            try:
                res = tr.translate_text([mask(t) for t in chunk], source_lang=L,
                                        target_lang=T, tag_handling="html")
                break
            except deepl.ConnectionException:
                time.sleep(3)
        else:
            raise SystemExit("DeepL fora do ar; rode de novo (o cache preserva o progresso).")
        for src, r in zip(chunk, res):
            out = unmask(r.text)
            ok = PROT.findall(out) == PROT.findall(src)
            if not ok: log("  tag danificada, mantido original:", plain(src)[:50])
            trans.setdefault(T, {})[src] = out if ok else src
        save_cache()
        log(f"  [{L}->{T}] {min(i + BATCH, len(texts))}/{len(texts)}")


# ---- aplica e exporta ----
for n in notes:
    mudou = False
    for f, v in n.items():
        c = chosen.get((n.mid, f))
        if c:
            new = trans.get(c[1], {}).get(v, v)
            if new != v:
                n[f] = new; mudou = True
    if mudou: col.update_note(n)
# ---- nomes dos baralhos (roda depois de tudo, antes de exportar) ----
DECK_CACHE_FILE = Path(__file__).resolve().parent / "deck_cache.json"  # separado do cache.json
SEP = "::"

def yes(msg, default="n"):
    return ask(f"{msg} (s/n)", default).lower() in ("s", "sim", "y", "yes")

def choose(title, opts, default):
    print(f"\n{title}")
    for k, v in opts.items():
        print(f"  {k}) {v}")
    while True:
        r = ask("Escolha", default)
        if r in opts:
            return r
        print("  Opção inválida.")

def load_deck_cache():
    try:
        data = json.loads(DECK_CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("version") == 1 and isinstance(data.get("names"), dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError):
        bak = DECK_CACHE_FILE.with_name(DECK_CACHE_FILE.name + ".bak")
        DECK_CACHE_FILE.replace(bak)
        log(f"deck_cache.json estava ilegível; guardei uma cópia em {bak.name}")
    return {"version": 1, "names": {}}

def save_deck_cache(data):
    tmp = DECK_CACHE_FILE.with_name(DECK_CACHE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DECK_CACHE_FILE)

def clean_seg(s, fallback):
    s = s.replace(SEP, ":").strip()
    return s or fallback

def auto_names(segments, src_lang, dcache):
    """Traduz segmentos soltos (nunca o caminho completo). Só a saída bruta do DeepL
    vai para o cache; nomes digitados à mão nunca são gravados."""
    bucket = (dcache["names"].setdefault(TARGET, {})
                             .setdefault(src_lang or "auto", {}))
    pending = sorted(s for s in segments if s not in bucket)
    log(f"nomes de baralhos: {len(pending)} a traduzir "
        f"({len(segments) - len(pending)} já no cache)")
    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        for _ in range(3):
            try:
                res = tr.translate_text(chunk, source_lang=src_lang, target_lang=TARGET)
                break
            except deepl.ConnectionException:
                time.sleep(3)
        else:
            log("DeepL fora do ar; nomes restantes ficam como estão")
            break
        for s, r in zip(chunk, res):
            already = r.detected_source_lang == TBASE        # já está no idioma de saída
            bucket[s] = s if already else clean_seg(r.text, s)
        save_deck_cache(dcache)
    return {s: bucket.get(s, s) for s in segments}

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

def pick_scope(decks):
    """Devolve o conjunto de prefixos (tuplas de segmentos) cujo nome será traduzido."""
    paths = sorted({tuple(n.split(SEP)) for _, n in decks},
                   key=lambda p: tuple(s.lower() for s in p))
    scope = choose("Quais baralhos traduzir?",
                   {"1": "Só os baralhos principais (subbaralhos mantêm o nome)",
                    "2": "Todos os baralhos e subbaralhos",
                    "3": "Escolher por número"}, "2")
    if scope == "1":
        return {p[:1] for p in paths}
    if scope == "2":
        return {p[:i + 1] for p in paths for i in range(len(p))}
    print()
    for i, p in enumerate(paths, 1):
        print(f" {i:>3}) {'  ' * (len(p) - 1)}{p[-1]}")
    while True:
        raw = ask("Números (ex.: 1,3,5-8; n = cancelar)", "n")
        if raw.lower() in ("n", "nao", "não"):
            return None
        nums = parse_numbers(raw, len(paths))
        if nums:
            return {paths[k - 1] for k in nums}      # só o último segmento de cada um muda
        print("  Seleção inválida.")

def build_newseg(selected, method, src_lang, dcache):
    ordered = sorted(selected, key=lambda p: tuple(s.lower() for s in p))
    auto = {}
    if method in ("1", "3"):
        auto = auto_names({p[-1] for p in selected}, src_lang, dcache)
    newseg, memo = {}, {}
    for p in ordered:
        seg = p[-1]
        if method == "1":
            new = auto.get(seg, seg)
        else:
            suggestion = memo.get(seg) or (auto.get(seg, seg) if method == "3" else seg)
            while True:
                new = ask(f"  {SEP.join(p)}", suggestion)
                if new and SEP not in new:
                    break
                print(f"  Nome vazio ou contendo '{SEP}' não é válido.")
            memo[seg] = new                       # mesmo segmento -> mesma sugestão
        if new != seg:
            newseg[p] = new
    return newseg

def final_name(name, newseg):
    p = tuple(name.split(SEP))
    return SEP.join(newseg.get(p[:i + 1], p[i]) for i in range(len(p)))

def rename_decks():
    decks = [(d.id, d.name) for d in col.decks.all_names_and_ids(
        skip_empty_default=True, include_filtered=False) if d.name != "Default"]
    if not decks or not yes("\nTraduzir/alterar nomes de baralhos?", "n"):
        return
    selected = pick_scope(decks)
    if not selected:
        return
    src_lang = next(iter(picked)) if len(picked) == 1 else None
    dcache = load_deck_cache()

    while True:
        method = choose("Como alterar os nomes?",
                        {"1": "Automático (DeepL)",
                         "2": "Manual (você digita; Enter mantém o atual)",
                         "3": "Revisar um a um (sugestão do DeepL; Enter aceita)"}, "1")
        newseg = build_newseg(selected, method, src_lang, dcache)
        plan = {did: (old, final_name(old, newseg)) for did, old in decks}
        plan = {did: v for did, v in plan.items() if v[0] != v[1]}
        if not plan:
            log("nenhum nome de baralho mudou")
            return

        print("\n=== Nomes de baralhos ===")
        for old, new in sorted(plan.values()):
            print(f"  {old}  ->  {new}")

        finals = collections.defaultdict(list)       # Anki ignora maiúsculas/minúsculas
        for did, old in decks:
            finals[(plan[did][1] if did in plan else old).lower()].append(did)
        dups = [k for k, v in finals.items() if len(v) > 1]
        if dups:
            print("\nConflito: mais de um baralho ficaria com o mesmo nome:")
            for k in dups:
                print(f"  {k}")
            if not yes("Tentar de novo?", "s"):
                return
            continue
        if yes("\nAplicar?", "s"):
            break
        if not yes("Tentar de novo?", "n"):
            return

    done = 0
    for did, (old, new) in sorted(plan.items(), key=lambda kv: kv[1][0].count(SEP)):
        if col.decks.name(did) == new:               # o pai já arrastou o filho
            done += 1
            continue
        try:
            col.decks.rename(did, new)               # pais antes dos filhos
            done += 1
        except Exception as e:
            log(f"  não consegui renomear '{old}': {e}")
    log(f"baralhos renomeados: {done}/{len(plan)}")

rename_decks()
log("exportando...")
col.export_anki_package(out_path=OUT, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False), limit=None)
col.close()
log("pronto ->", OUT)
