import os
from pathlib import Path
import re
import shutil
import sys
import time

from ankitool.core.constants import DEFAULT_PROVIDER
from ankitool.core.settings import configured_provider
from ankitool.core.errors import Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.progress import Reporter
from ankitool.providers.base import PROVIDERS, make_provider


def paint(text, code):
    """ANSI colour; off when output is redirected or NO_COLOR is set."""
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return str(text)

def head(t): return paint(t, "1;36")          # titles: bold cyan
def num(t): return paint(t, "1;33")           # option numbers: bold yellow
def dim(t): return paint(t, "2")              # help text: dim
def idx(i): return num(f"{i:>3})")            # "  1)"


class ConsoleReporter(Reporter):
    def __init__(self, quiet=False, cancel=None):
        super().__init__(cancel)
        self.quiet = quiet
        self._last, self._next = {}, {}

    def log(self, *a):
        if not self.quiet:
            print(time.strftime("%H:%M:%S"), *a, flush=True)

    def warn(self, *a):                                   # shown even with --quiet
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

YES_WORDS = ("s", "sim", "y", "yes")
NO_WORDS = ("n", "nao", "não", "no")

class Ui:
    """Console input/output: log, questions, menus and progress bar.
    With Ui.auto = True (the --yes option), no questions are asked: the default value is used."""

    auto = False

    @staticmethod
    def ask(msg, default):
        if Ui.auto:
            return default
        return input(f"{msg} [{default}]: ").strip().strip('"') or default

    @staticmethod
    def yes(msg, default="n"):
        """Yes/no question. Only accepts y/n (or variations); Enter uses the default."""
        if Ui.auto:
            return default.lower() in YES_WORDS
        label = "yes" if default.lower() in YES_WORDS else "no"
        while True:
            r = input(f"{msg}\n  Type y (yes) or n (no). Just Enter = {label}: ").strip().lower()
            if r == "":
                r = default.lower()
            if r in YES_WORDS:
                return True
            if r in NO_WORDS:
                return False
            print("  Not understood. Type y for yes or n for no.")

    @staticmethod
    def choose(title, opts, default):
        """Single-choice menu. opts: {"1": "Text"} or {"1": ("Text", "short explanation")}."""
        print(f"\n{head(title)}")
        for k, v in opts.items():
            label, desc = v if isinstance(v, tuple) else (v, None)
            mark = dim("  (default)") if k == default else ""
            print(f"  {num(k + ')')} {label}{mark}")
            if desc:
                print(dim(f"       {desc}"))
        print(dim("\nType a number and press Enter."))
        while True:
            r = Ui.ask("Choice", default)
            if r in opts:
                return r
            print(f"  Invalid option. Type one of these numbers: {', '.join(opts)}.")

    @staticmethod
    def parse_numbers(raw, n):
        """'1,3,5-8' -> {1,3,5,6,7,8}; None if anything is outside 1..n."""
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
        """Numbered list to choose items. Returns a list of indices (starting at 0),
        or None if the person cancelled (0).
        default: 'all' (Enter = all), 'first' (Enter = item 1) or None (Enter is not allowed).
        aliases: {UPPERCASE_TEXT: index}, to also accept typed codes."""
        n = len(labels)
        if Ui.auto:
            return list(range(n)) if default == "all" else [0] if default == "first" else None
        #print(f"\n{title}")
        print(f"\n{head(title)}")
        if intro:
            print(dim(intro))
        print()
        for i, lab in enumerate(labels, 1):
            print(f"{idx(i)} {lab}")
        if cancel:
            print(f"{num('  0)')} {dim('Cancel')}")
        tip = "Type a number" + (", several (1,3) or a range (2-5)" if multi else "")
        tip += {"all": ". Enter = all.", "first": ". Enter = item 1."}.get(default, ".")
        print("\n" + dim(tip))
        if hint:
            print(dim(hint))
        while True:
            raw = input("Your choice: ").strip().strip('"')
            if raw == "":
                if default == "all":
                    return list(range(n))
                if default == "first":
                    return [0]
                print("  I need an answer. Type a number from the list.")
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
                print("  Only one item can be chosen here. Type a single number.")
            else:
                print(f"  I didn't understand '{', '.join(bad) or raw}'. Type numbers from 1 to {n}"
                      f"{', or 0 to cancel' if cancel else ''}.")

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
    """Starting folder for the picker: the current folder, or the script's folder if only .apkg files are there."""
    def has_apkg(d):
        try:
            return any(p.suffix.lower() == ".apkg" for p in d.iterdir() if p.is_file())
        except OSError:
            return False
    cwd = Path.cwd()
    if has_apkg(cwd) or not has_apkg(SCRIPT_DIR):
        return cwd
    return SCRIPT_DIR

def read_key():
    """One key press: 'up','down','left','right','pgup','pgdn','home','end','enter','esc',
    'backspace', or the typed character."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):                      # arrow/function keys come as two characters
            return {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pgup",
                    "Q": "pgdn", "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        if ch == "\x03":
            raise KeyboardInterrupt
        return {"\r": "enter", "\x1b": "esc", "\x08": "backspace"}.get(ch, ch)
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch == "\x1b":
            seq = ""
            while select.select([fd], [], [], 0.05)[0]:
                seq += os.read(fd, 1).decode(errors="ignore")
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "[5~": "pgup",
                    "[6~": "pgdn", "[H": "home", "[F": "end", "[1~": "home", "[4~": "end",
                    "OH": "home", "OF": "end"}.get(seq, "esc")
        if ch == "\x03":
            raise KeyboardInterrupt
        return {"\r": "enter", "\n": "enter", "\x7f": "backspace"}.get(ch, ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _entries(cur, suffix):
    """[(label, path, is_dir)]: '..' first, then folders, then files with the given suffix."""
    items = []
    if cur.parent != cur:
        items.append(("..", cur.parent, True))
    try:
        found = sorted(cur.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return items
    for p in found:
        try:
            if p.is_dir() and not p.name.startswith("."):
                items.append((p.name + "/", p, True))
            elif p.is_file() and p.suffix.lower() == suffix:
                items.append((p.name, p, False))
        except OSError:
            continue
    return items


def pick_file(start=None, suffix=".apkg"):
    """File browser driven by the keyboard: Up/Down move, Enter opens a folder or chooses a file.
    Returns the chosen Path, or None if cancelled."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _pick_file_numbered(start, suffix)       # no keyboard control: numbered list
    os.system("")                                       # enables ANSI codes on old Windows consoles
    cur = Path(start or Path.cwd()).resolve()
    pos, focus = 0, None
    while True:
        items = _entries(cur, suffix)
        if focus is not None:                           # came back up: select the folder we left
            pos = next((i for i, it in enumerate(items) if it[1] == focus), 0)
            focus = None
        pos = max(0, min(pos, len(items) - 1))
        rows = max(5, shutil.get_terminal_size().lines - 6)
        top = max(0, min(pos - rows // 2, len(items) - rows))
        out = ["\033[2J\033[H" + head(f"=== Choose a {suffix} file ==="), dim(str(cur)), ""]
        for i in range(top, min(top + rows, len(items))):
            label, p, is_dir = items[i]
            if is_dir:
                text = paint(label, "36")
            else:
                try:
                    text = f"{label}  {dim(f'({p.stat().st_size / 1e6:.1f} MB)')}"
                except OSError:
                    text = label
            out.append(f" {num('>')} \033[7m {text} \033[0m" if i == pos else f"     {text}")
        if len(items) <= 1:
            out.append(dim(f"     (no folders or {suffix} files here)"))
        out += ["", dim(f"{pos + 1}/{len(items)}   Up/Down move   Enter open/choose   "
                        f"Left/Backspace up   p paste path   Esc cancel")]
        print("\n".join(out), end="", flush=True)

        key = read_key()
        if key == "up":
            pos -= 1
        elif key == "down":
            pos += 1
        elif key == "pgup":
            pos -= rows
        elif key == "pgdn":
            pos += rows
        elif key == "home":
            pos = 0
        elif key == "end":
            pos = len(items) - 1
        elif key in ("enter", "right") and items:
            _, p, is_dir = items[pos]
            if is_dir:
                focus = cur if p == cur.parent else None
                cur, pos = p, 0
            elif key == "enter":
                print()
                return p
        elif key in ("left", "backspace") and cur.parent != cur:
            focus, cur = cur, cur.parent
        elif key in ("esc", "q"):
            print()
            return None
        elif key in ("p", "/"):
            raw = input("\n\nPath of a folder or file: ").strip().strip('"').strip("'")
            p = Path(raw).expanduser() if raw else None
            if p is not None and not p.is_absolute():
                p = cur / p
            try:
                if p is not None and p.resolve().is_dir():
                    cur, pos = p.resolve(), 0
                elif p is not None and p.resolve().is_file() and p.suffix.lower() == suffix:
                    return p.resolve()
            except OSError:
                pass

def _pick_file_numbered(start=None, suffix=".apkg"):
    """Terminal file browser. Shows the folders and .apkg files of the current folder.
    Returns the Path of the chosen file, or None if the person cancelled."""
    cur = Path(start or Path.cwd()).resolve()
    while True:
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            print(f"\n  Could not open the folder {cur}. Going up one folder.")
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

        print(f"\n=== Choose a {suffix} file ===")
        print(f"You are in the folder: {cur}\n")
        items = []
        if cur.parent != cur:
            print("   ..) Go up one folder (back)")
        for p in dirs:
            items.append(p)
            print(f"  {len(items):>3}) [folder]  {p.name}")
        for p in files:
            items.append(p)
            try:
                size = f"  ({p.stat().st_size / 1e6:.1f} MB)"
            except OSError:
                size = ""
            print(f"  {len(items):>3}) {p.name}{size}")
        if not items:
            print(f"      (there are no folders or {suffix} files here)")
        print("    0) Cancel (don't choose any file)")

        print("\nHow to use it:")
        print("  - Number of a [folder]: go into that folder.")
        print(f"  - Number of a {suffix} file: choose that file.")
        print("  - Two dots (..): go up one folder.")
        print(f"  - Or paste the full path of a {suffix} file (or of a folder).")
        raw = input("Your choice: ").strip().strip('"').strip("'")

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
            faixa = f"from 1 to {len(items)}, " if items else ""
            print(f"\n  Number out of the list. Type a number {faixa}'..' or 0.")
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
            print(f"\n  Could not find '{raw}' as a folder or as a {suffix} file.")
            continue
        print("\n  I need an answer. Type a number, '..' or 0.")

def choose_provider(opts):
    """Translation service: --provider, or a question (if there is more than one), or the default."""
    name = getattr(opts, "provider", None) or configured_provider()   # flag > env/config > ask
    visible = sorted(n for n, c in PROVIDERS.items() if not c.hidden)
    if not name and len(visible) > 1 and not Ui.auto:
        keys = {str(i): n for i, n in enumerate(visible, 1)}
        default = next(k for k, n in keys.items() if n == DEFAULT_PROVIDER)
        key = Ui.choose("Which translation service do you want to use?",
                        {k: PROVIDERS[n].label for k, n in keys.items()}, default)
        name = keys[key]
    return make_provider(name or DEFAULT_PROVIDER)

def ask_input(opts):
    """Reads the input .apkg from the options or opens the file picker."""
    if opts.input:
        src = Path(opts.input).expanduser().resolve()
    elif Ui.auto:
        raise ConfigError("With -y/--yes you must give the input .apkg file.")
    else:
        print("\nFirst, choose the deck (.apkg file) you want to use.")
        src = pick_file(default_start())
        if src is None:
            raise Cancelled("Cancelled.")
    if not src.exists():
        raise ConfigError(f"File not found: {src}")
    return src

def ask_paths(opts, suffix="_out"):
    """Chooses the input .apkg (picker) and the name of the output .apkg."""
    src = ask_input(opts)
    if opts.output:
        out = Path(opts.output).expanduser()
    else:
        default = f"{src.stem}{suffix}.apkg"
        if Ui.auto:
            name = default
        else:
            print("\nNow choose the name of the NEW file I'll create. The original file is not changed.")
            print(f"It will be saved in the same folder as the original: {src.parent}")
            name = Ui.ask("Name of the new file (just Enter = use the suggested name)", default)
        out = Path(name).expanduser()
    if not out.is_absolute() and out.parent == Path("."):
        out = src.parent / out                            # simple name: saved next to the original
    out = out.resolve()
    if out.suffix.lower() != ".apkg":
        out = out.with_name(out.name + ".apkg")
    if out == src:
        raise ConfigError("The new file cannot have the same name as the original.")
    if out.exists() and not opts.force:
        if Ui.auto:
            raise ConfigError(f"The file '{out}' already exists. Use --force to overwrite it "
                              f"or choose another name with -o.")
        if not Ui.yes(f"\nA file named '{out.name}' already exists. Do you want to overwrite it?", "n"):
            raise Cancelled("Cancelled: run again and choose another name.")
    return src, out
