# pip install anki deepl
import json, os, re, time, random, tempfile, collections, deepl
from anki.collection import (Collection, ImportAnkiPackageRequest,
    ImportAnkiPackageOptions, ExportAnkiPackageOptions)

KEY = (os.environ.get("DEEPL_KEY") or "COLE-A-CHAVE-AQUI:fx").strip().strip('"')
BATCH, SAMPLE = 40, 15

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def ask(msg, default):
    return input(f"{msg} [{default}]: ").strip().strip('"') or default
def lang_or_none(msg, default):
    r = (input(f"{msg} [{default}]: ").strip() or default).upper()
    return None if r in ("-", "N", "NAO", "NÃO") else r

# ---- perguntas ----
SRC = os.path.abspath(ask("Arquivo de entrada", "italian.apkg"))
if not os.path.exists(SRC):
    raise SystemExit(f"Arquivo não encontrado: {SRC}")
base = os.path.splitext(os.path.basename(SRC))[0]
OUT = os.path.abspath(ask("Arquivo de saída", f"{base}_out.apkg"))
if not OUT.lower().endswith(".apkg"):
    OUT += ".apkg"

SRC_LANG = ask("Idioma de origem (auto = detectar)", "auto").upper()
SIDE = ask("Qual lado traduzir? (f = frente, v = verso, a = ambos)", "v").lower()
T_BACK = T_FRONT = None
TARGET = ask("Idioma de saída", "PT-BR").upper()
if SIDE in ("f", "a"): T_FRONT = TARGET
if SIDE in ("v", "a"): T_BACK = TARGET
if not (T_FRONT or T_BACK):
    raise SystemExit("Opção inválida: use f, v ou a.")

tr = deepl.Translator(KEY)
log("uso atual DeepL:", tr.get_usage().character)
tcodes = {l.code for l in tr.get_target_languages()}
for t in (T_FRONT, T_BACK):
    if t and t not in tcodes:
        raise SystemExit(f"Idioma de saída inválido: {t}. Opções: {sorted(tcodes)}")
if SRC_LANG != "AUTO":
    scodes = {l.code for l in tr.get_source_languages()}
    if SRC_LANG.split("-")[0] not in scodes:
        raise SystemExit(f"Idioma de origem inválido. Opções: {sorted(scodes)} ou auto")
    SRC_LANG = SRC_LANG.split("-")[0]

# ---- proteção de tags ----
PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)
def mask(t):   return PROT.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', t)
def unmask(t): return WRAP.sub(r"\1", t)
def plain(s):  return re.sub(r"\[sound:[^\]]+\]|<[^>]+>|&\w+;|\{\{c\d+::|\}\}", "", s).strip()

# ---- frente / verso, a partir dos templates do tipo de nota ----
def refs(fmt, name):
    return re.search(r"\{\{[#^/]?(?:[^{}:]+:)*" + re.escape(name) + r"\}\}", fmt) is not None

def sides(model):
    front, back = set(), set()
    for t in model["tmpls"]:
        for name in (f["name"] for f in model["flds"]):
            if refs(t["qfmt"], name): front.add(name)
            elif refs(t["afmt"], name): back.add(name)
    return front, back - front

log("importando", SRC)
col = Collection(os.path.join(tempfile.mkdtemp(), "collection.anki2"))
col.import_anki_package(ImportAnkiPackageRequest(
    package_path=SRC, options=ImportAnkiPackageOptions()))
notes = [col.get_note(n) for n in col.find_notes("")]
log("notas:", len(notes))

allowed, names = {}, {}      # mid -> {campo: idioma de saída}
for n in notes:
    if n.mid not in allowed:
        m = n.note_type()
        fr, bk = sides(m)
        allowed[n.mid] = {**({f: T_FRONT for f in fr} if T_FRONT else {}),
                          **({f: T_BACK for f in bk} if T_BACK else {})}
        names[n.mid] = m["name"]
        log(f"  {m['name']}: frente={sorted(fr)} verso={sorted(bk)}")

vals = collections.defaultdict(list)
for n in notes:
    for f, v in n.items():
        if f in allowed[n.mid] and len(plain(v)) >= 3:
            vals[(n.mid, f)].append(v)

# ---- detecta o idioma de cada campo ----
chosen = {}   # (mid, campo) -> (idioma de origem, idioma de saída)
for (mid, f), vs in vals.items():
    target = allowed[mid][f]
    tbase = target.split("-")[0]
    sample = random.sample(vs, min(SAMPLE, len(vs)))
    res = tr.translate_text([plain(s) for s in sample], target_lang=target)
    lang, cnt = collections.Counter(r.detected_source_lang for r in res).most_common(1)[0]
    ratio = cnt / len(res)
    ok = ratio >= 0.6 and lang != tbase and (SRC_LANG == "AUTO" or lang == SRC_LANG)
    log(f"  {names[mid]} / {f}: {lang} ({ratio:.0%}) -> {'TRADUZ p/ ' + target if ok else 'mantém'}")
    if ok:
        chosen[(mid, f)] = (lang, target)

# ---- traduz (cache por idioma de saída e de origem) ----
cache_file = "cache.json"
cache = json.load(open(cache_file, encoding="utf-8")) if os.path.exists(cache_file) else {}
todo = collections.defaultdict(set)   # (origem, saída) -> textos
for (mid, f), (L, T) in chosen.items():
    for v in vals[(mid, f)]:
        if f"{T}|{L}|{v}" not in cache:
            todo[(L, T)].add(v)
log("a traduzir:", sum(len(s) for s in todo.values()))

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
            cache[f"{T}|{L}|{src}"] = out if ok else src
        json.dump(cache, open(cache_file, "w", encoding="utf-8"), ensure_ascii=False)
        log(f"  [{L}->{T}] {min(i + BATCH, len(texts))}/{len(texts)}")

# ---- aplica e exporta ----
for n in notes:
    mudou = False
    for f, v in n.items():
        c = chosen.get((n.mid, f))
        if c:
            new = cache.get(f"{c[1]}|{c[0]}|{v}", v)
            if new != v:
                n[f] = new; mudou = True
    if mudou: col.update_note(n)

log("exportando...")
col.export_anki_package(out_path=OUT, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False), limit=None)
col.close()
log("pronto ->", OUT)
