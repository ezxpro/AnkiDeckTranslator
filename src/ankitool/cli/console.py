from pathlib import Path
import re
import sys
import time

from ankitool.core.constants import DEFAULT_PROVIDER
from ankitool.core.errors import Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.progress import Reporter
from ankitool.providers.base import PROVIDERS, make_provider


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

def choose_provider(opts):
    """Serviço de tradução: --provider, ou pergunta (se houver mais de um), ou o padrão."""
    name = getattr(opts, "provider", None)
    visible = sorted(n for n, c in PROVIDERS.items() if not c.hidden)
    if not name and len(visible) > 1 and not Ui.auto:
        keys = {str(i): n for i, n in enumerate(visible, 1)}
        default = next(k for k, n in keys.items() if n == DEFAULT_PROVIDER)
        key = Ui.choose("Qual serviço de tradução você quer usar?",
                        {k: PROVIDERS[n].label for k, n in keys.items()}, default)
        name = keys[key]
    return make_provider(name or DEFAULT_PROVIDER)

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
