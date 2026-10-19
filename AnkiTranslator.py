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

log("exportando...")
col.export_anki_package(out_path=OUT, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False), limit=None)
col.close()
log("pronto ->", OUT)
