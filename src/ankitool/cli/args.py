from pathlib import Path
import argparse
import os
import re
import sys
import textwrap

from ankitool.cache.translation import MODES
from ankitool.core.constants import AUDIO_MAX_CHARS as MAX_CHARS, DEFAULT_PROVIDER, MIN_RATIO, SAMPLE, SEP, VERSION
from ankitool.core.errors import ConfigError
from ankitool.core.settings import configured_provider
from ankitool.providers.base import PROVIDERS
from ankitool.tts.base import SPEECH


class WrapHelp(argparse.HelpFormatter):
    """Wraps the prose of descriptions; lines that start with a space (examples, key lists)
    and blank lines are kept exactly as written."""

    def _fill_text(self, text, width, indent):
        out = []
        for para in text.split("\n"):
            if not para.strip() or para.startswith(" "):
                out.append(para)
            else:
                out.append(textwrap.fill(para, width, initial_indent=indent, subsequent_indent=indent))
        return "\n".join(out)


def colorize(text):
    """Adds colors to help text: usage line, section headers and option flags."""
    def style(t, code):
        return f"\033[{code}m{t}\033[0m"
    out = []
    for line in text.splitlines():
        if line.startswith("usage:"):
            line = style("usage:", "1;33") + line[6:]
        elif line and not line.startswith(" ") and line.endswith(":"):
            line = style(line, "1;36")
        else:
            line = re.sub(r"(?<![\w/])(-{1,2}[A-Za-z][\w-]*)", lambda m: style(m.group(1), "32"), line)
            if line.lstrip().startswith("ankitool"):
                line = line.replace("ankitool", style("ankitool", "1;33"), 1)
        out.append(line)
    return "\n".join(out) + "\n"


COMMANDS = ("translate", "audio", "reverse", "info", "langs", "bench", "config", "cache")


class FriendlyParser(argparse.ArgumentParser):
    """ArgumentParser that prints short errors followed by the relevant help text.

    On bad input, parse_args() calls error(); instead of the raw usage dump we print a
    concise message and then the help of the command being used (or the top-level help),
    so the user immediately sees what arguments are available. The SystemExit it raises
    is still caught by main() (which re-raises it outside the menu)."""

    def print_help(self, file=None):
        """Help with colors when printed to a terminal (off with NO_COLOR=1 or when redirected)."""
        file = file or sys.stdout
        text = self.format_help()
        if file.isatty() and not os.environ.get("NO_COLOR"):
            text = colorize(text)
        file.write(text)

    def error(self, message):
        """Print a short message followed by the relevant help, so a bad invocation
        immediately shows the user what arguments the command takes."""
        # Shorten argparse's "the following arguments are required: X" phrasing.
        message = message.replace("the following arguments are required: ", "missing: ")
        # Shorten the noisy "argument action: invalid choice: ..." phrasing.
        message = message.replace("argument ", "").replace("invalid choice: ", "")
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        # Show the help of the *deepest* known subcommand (e.g. "config"), so the user
        # sees that command's arguments rather than the top-level list.
        sub = self
        for _a in self._actions:
            if (isinstance(_a, argparse._SubParsersAction)
                    and _a.choices and getattr(_a, "dest", None) == "command"):
                picked = next((v for k, v in _a.choices.items() if k in sys.argv), None)
                if picked is not None:
                    sub = picked
        sub.print_help(sys.stderr)
        self.exit(2)


def _looks_like_input(token):
    """True if token is plausible as the shortcut's input .apkg (not a mistyped command)."""
    return token.lower().endswith(".apkg") or "/" in token or "\\" in token or token in (".", "..")

def positive_int(s):
    try:
        v = int(s)
    except ValueError:
        v = 0
    if v < 1:
        raise argparse.ArgumentTypeError(f"'{s}' is not an integer greater than zero")
    return v

def workers_type(s):
    try:
        v = sorted({int(x) for x in s.split(",")})
    except ValueError:
        v = []
    if not v or v[0] < 1:
        raise argparse.ArgumentTypeError(f"'{s}' is not a list of integers greater than zero (e.g. 1,2,4)")
    return v

def ratio_type(s):
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        v = 0.0
    if not 0 < v <= 1:
        raise argparse.ArgumentTypeError(f"'{s}' is not between 0 and 1 (e.g. 0.6)")
    return v

def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "-V", "--version"):
        if _looks_like_input(argv[0]):
            argv.insert(0, "translate")                  # shortcut: script deck.apkg -t PT-BR
        else:
            # A bare word that is neither a command nor a file: probably a typo.
            raise ConfigError(
                f"Unknown command '{argv[0]}'. Available: {', '.join(COMMANDS)}. "
                f"Use '-h' for help, or a .apkg file to translate it directly."
            )

    p = FriendlyParser(
        prog=Path(sys.argv[0]).name,
        description="Tools for Anki .apkg decks. With no arguments, opens the "
                    "interactive mode (menu). Use 'COMMAND -h' to see each command's options.",
        epilog="For any command, whatever is not given as an option is asked for, "
               "unless -y/--yes is used.")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=FriendlyParser)

    io = FriendlyParser(add_help=False)
    io.add_argument("input", nargs="?", metavar="INPUT", help="input .apkg file")
    common = FriendlyParser(add_help=False)
    common.add_argument("-y", "--yes", action="store_true",
                        help="don't ask anything: use each command's defaults")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="only show warnings and the final result (no progress messages)")
    run_opts = FriendlyParser(add_help=False, parents=[common])
    run_opts.add_argument("-o", "--output", metavar="OUTPUT", help="output .apkg file")
    run_opts.add_argument("-f", "--force", action="store_true",
                          help="overwrite the output file if it already exists "
                               "(otherwise, with -y, the program stops with an error)")
    run_opts.add_argument("--keep-scheduling", action="store_true",
                          help="keep study history (dates, intervals) and deck options "
                               "in the generated file (default: removed)")
    run_opts.add_argument("--legacy", action=argparse.BooleanOptionalAction, default=True,
                          help="legacy .apkg format, accepted by any Anki/"
                               "AnkiDroid version (default); --no-legacy writes the newer, smaller format")

    t = sub.add_parser(
        "translate", parents=[io, run_opts],
        help="translate fields (and optionally deck names)",
        description="Translates the fields of a deck with a translation service (--provider). "
                    "Default output: <input>_out.apkg. "
                    "With -y: PT-BR, all detected languages, deck names unchanged.")
    t.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                   help=f"translation service: {', '.join(sorted(PROVIDERS))} "
                        f"(default: {configured_provider() or DEFAULT_PROVIDER})")
    t.add_argument("-t", "--target", metavar="CODE",
                   help="target language, e.g. PT-BR, EN-US (see the 'langs' command)")
    t.add_argument("-l", "--lang", metavar="CODES",
                   help="source language(s) to translate, comma-separated "
                        "(e.g. IT,EN) or 'all' for all detected ones")
    t.add_argument("-d", "--rename-decks", choices=["none", "main", "all"],
                   help="deck names: none = leave unchanged, main = top-level decks only, "
                        "all = top-level and sub-decks")
    t.add_argument("-m", "--deck-method", choices=["auto", "manual", "review", "keep"],
                   help="how to rename decks: auto (by the service), manual (you type), "
                        "review (service suggestion, one by one) or keep (only --deck-name ones)")
    t.add_argument("--deck-name", action="append", metavar="CURRENT=NEW",
                   help="renames the deck named CURRENT (one level, no '::') to NEW; "
                        "can be repeated. Without -d/-m, only these are renamed")
    t.add_argument("--cache-mode", choices=list(MODES), metavar="MODE",
                   help="which saved translations are reused: shared = any service's (this service's "
                        "first, then another's); provider = any model of this service; isolated = only this service and model. Default: the one set "
                        "in config.json (providers.SERVICE.cache), or shared"),
    t.add_argument("--dry-run", action="store_true",
                   help="only calculates how many texts and characters would be translated; "
                        "does not translate or create a file (language detection still uses some characters)")
    t.add_argument("--field", action="append", dest="only_fields", metavar="FIELD",
                   help="translate ONLY this field (or NOTETYPE:FIELD); can be repeated")
    t.add_argument("--skip-field", action="append", dest="skip_fields", metavar="FIELD",
                   help="do NOT translate this field (or NOTETYPE:FIELD); can be repeated")
    t.add_argument("--sample", type=positive_int, default=SAMPLE, metavar="N",
                   help=f"how many texts from each field to use for language detection (default: {SAMPLE})")
    t.add_argument("--min-ratio", type=ratio_type, default=MIN_RATIO, metavar="R",
                   help=f"minimum fraction (0 to 1) of the sample that must agree on the language "
                        f"for the field to be considered reliable (default: {MIN_RATIO})")
    t.add_argument("--cache-dir", metavar="FOLDER",
                   help="folder for the cache files (default: the script's folder)")

    a = sub.add_parser(
        "audio", parents=[io, run_opts],
        help="add spoken audio to notes that have none",
        description="Generates audio from the text of the fields with a speech service (--speech) and "
                    "appends it to the SAME field as [sound:...], so it plays on the side of the card "
                    "that shows the text. Only notes with no [sound:] in any field are changed; cloze "
                    "notes are skipped. Generated files are kept in the 'audio' folder of the cache "
                    "folder, so a repeated text is never requested twice and an interrupted run "
                    "continues where it stopped. Default output: <input>_audio.apkg.",
        epilog="examples:\n"
               "  ankitool audio deck.apkg --lang IT --dry-run     how much would be generated\n"
               "  ankitool audio deck.apkg --lang IT               choose nothing else, detect fields\n"
               "  ankitool audio deck.apkg --lang IT --field Word  only the Word field, no detection\n"
               "  ankitool audio deck.apkg --lang IT --voice it-IT-IsabellaNeural",
        formatter_class=WrapHelp)
    a.add_argument("-s", "--speech", choices=sorted(SPEECH), default="azure", metavar="SERVICE",
                   help=f"speech service: {', '.join(sorted(SPEECH))} (default: azure)")
    a.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                   help=f"translation service used only to detect each field's language "
                        f"(default: {configured_provider() or DEFAULT_PROVIDER}); not needed with --lang and --field")
    a.add_argument("-l", "--lang", metavar="CODE",
                   help="language to read aloud, e.g. IT. Without it you are asked (required with -y)")
    a.add_argument("--voice", metavar="NAME",
                   help="voice to use instead of the default one for the language, e.g. it-IT-IsabellaNeural")
    a.add_argument("--field", action="append", dest="only_fields", metavar="FIELD",
                   help="add audio ONLY to this field (or NOTETYPE:FIELD); can be repeated. "
                        "With --lang, the language is not detected")
    a.add_argument("--skip-field", action="append", dest="skip_fields", metavar="FIELD",
                   help="do NOT add audio to this field (or NOTETYPE:FIELD); can be repeated")
    a.add_argument("--include-cloze", action="store_true",
                   help="also add audio to cloze notes (it will read the hidden answer)")
    a.add_argument("--max-chars", type=positive_int, default=MAX_CHARS, metavar="N",
                   help=f"skip fields whose text is longer than N characters (default: {MAX_CHARS})")
    a.add_argument("--dry-run", action="store_true",
                   help="only counts what would be generated; creates no audio and no file "
                        "(language detection still uses some characters)")
    a.add_argument("--sample", type=positive_int, default=SAMPLE, metavar="N",
                   help=f"how many texts from each field to use for language detection (default: {SAMPLE})")
    a.add_argument("--min-ratio", type=ratio_type, default=MIN_RATIO, metavar="R",
                   help=f"minimum fraction (0 to 1) of the sample that must agree on the language "
                        f"(default: {MIN_RATIO})")
    a.add_argument("--cache-dir", metavar="FOLDER",
                   help="folder for cache.sqlite and the 'audio' folder (default: the script's folder)")

    r = sub.add_parser(
        "reverse", parents=[io, run_opts],
        help="swap front and back of cards (no translation)",
        description="Swaps the front and back of the cards by exchanging the fields in the card templates. "
                    "The text is not changed, only which side each field shows on. Default output: "
                    "<input>_reversed.apkg. Running it again on the result undoes the change. "
                    "With -y: all note types (cloze is excluded). Use 'info' first to see the fields.",
        epilog="examples:\n"
               "  ankitool reverse deck.apkg                       every note type, suggested pairs\n"
               "  ankitool reverse deck.apkg --notetype Basic --swap Front=Back\n"
               "  ankitool reverse deck.apkg -o flipped.apkg -f    overwrite flipped.apkg if it exists",
        formatter_class=WrapHelp)
    r.add_argument("--notetype", action="append", metavar="NAME",
                   help="swap only this note type (can be repeated)")
    r.add_argument("--swap", action="append", metavar="FIELD1=FIELD2",
                   help="pair of fields that switch sides; can be repeated (default: pairs "
                        "suggested for each note type; see the 'info' command)")

    sub.add_parser("info", parents=[io, common],
                   help="shows note types, fields and decks of an .apkg",
                   description="Shows what is inside an .apkg: each note type with its fields and how many "
                               "notes use it, the front/back split and the suggested --swap pairs, and the "
                               "deck tree. Useful before 'translate --field' or 'reverse --swap'. "
                               "Creates no file and needs no API key.",
                   epilog="examples:\n"
                          "  ankitool info deck.apkg",
                   formatter_class=WrapHelp)
    lg = sub.add_parser("langs",
                        help="list the translation service's languages (target or source)",
                        description="Lists the languages a translation service accepts, as code and name. "
                                    "By default the TARGET languages (what you can translate into). With "
                                    "--source, the languages it can detect. Codes are what -t and --lang "
                                    "expect. Needs the service's API key.",
                        epilog="examples:\n"
                               "  ankitool langs -p deepl\n"
                               "  ankitool langs -p gemini --source",
                        formatter_class=WrapHelp)
    lg.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                    help=f"translation service (default: {configured_provider() or DEFAULT_PROVIDER})")
    lg.add_argument("--source", action="store_true",
                    help="list SOURCE languages (the ones the service recognizes) "
                         "instead of the target ones")

    b = sub.add_parser("bench", parents=[common],
                       help="measures a service's speed and rate limits (makes real API calls)",
                       description="Measures how fast a service is and where it starts to refuse requests. "
                                   "It makes real API calls with 1, 2, 4... parallel workers and reports "
                                   "speed and errors (rate limits), then suggests a max_workers value to save "
                                   "in config.json. It uses your API quota, so run it sparingly.",
                       epilog="examples:\n"
                              "  ankitool bench -p gemini\n"
                              "  ankitool bench -p deepl --calls 4 --batch 20 --workers 1,2",
                       formatter_class=WrapHelp)
    b.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                   help=f"translation service (default: {configured_provider() or DEFAULT_PROVIDER})")
    b.add_argument("-t", "--target", metavar="CODE", help="target language (default: the service's default)")
    b.add_argument("--calls", type=positive_int, default=8, metavar="N",
                   help="API calls per worker count (default: 8)")
    b.add_argument("--batch", type=positive_int, default=10, metavar="N",
                   help="texts per call (default: 10)")
    b.add_argument("--workers", type=workers_type, default=[1, 2, 4, 8], metavar="LIST",
                   help="worker counts to test, comma-separated (default: 1,2,4,8)")

    cdir = FriendlyParser(add_help=False)
    cdir.add_argument("--cache-dir", metavar="FOLDER",
                      help="folder of cache.sqlite (default: the script's folder)")
    ca = sub.add_parser("cache", parents=[common, cdir],
                        help="translation cache: which saved translations each service reuses",
                        description="Shows or changes how each service reuses the translations already in "
                                    "cache.sqlite. shared (default) = reuse any service's translation when "
                                    "its own is missing; provider = any model of its own service only; isolated = only its exact service and model. Set per service, "
                                    "or per run with translate --cache-mode.",
                        epilog="examples:\n"
                               "  ankitool cache                       show each service's mode\n"
                               "  ankitool cache use deepl isolated    DeepL reuses only its own translations\n"
                               "  ankitool cache use deepl --reset     DeepL back to shared\n"
                               "  ankitool cache select                choose from a menu (interactive)",
                        formatter_class=WrapHelp)
    ca_sub = ca.add_subparsers(dest="action", metavar="ACTION", parser_class=FriendlyParser)
    ca_sub.add_parser("show", parents=[common, cdir], help="show each service's mode and stored count",
                      description="Shows the cache file, the reuse mode of each service (from config.json, "
                                  "or the default shared), how many translations each service has stored, "
                                  "and the totals. Reads only; no API calls.",
                      epilog="example:\n  ankitool cache show",
                      formatter_class=WrapHelp)
    ca_sub.add_parser("select", parents=[common, cdir], help="choose service and mode from a menu",
                      description="Interactive: first pick the service, then its mode (shared or isolated). "
                                  "Saved to config.json. Same as 'cache use SERVICE MODE', without typing it. "
                                  "Needs a terminal (not -y).",
                      epilog="example:\n  ankitool cache select",
                      formatter_class=WrapHelp)
    ce = ca_sub.add_parser("export", parents=[common, cdir], help="write the cache to a JSON file",
                           description="Writes every saved text, its detected language and each saved "
                                       "translation (service, model, output) to a JSON file, for inspection. "
                                       "Reads only; no API calls.",
                           epilog="examples:\n"
                                  "  ankitool cache export cache.json\n"
                                  "  ankitool cache export ~/Desktop/cache.json",
                           formatter_class=WrapHelp)
    ce.add_argument("file", metavar="FILE", help="JSON file to write (asks before replacing one)")
    cl = ca_sub.add_parser("list", parents=[common, cdir], help="browse the cache in the terminal",
                           description="Prints saved texts and translations (service, model, output). "
                                       "Filters are ANDed. Reads only; no API calls.",
                           epilog="examples:\n"
                                  "  ankitool cache list                          scroll through everything\n"
                                  "  ankitool cache list -p deepl -t PT-BR -n 20  20 rows of DeepL's PT-BR\n"
                                  "  ankitool cache list -o cache.txt             full text, to a file\n"
                                  "  ankitool cache list --text haus -o out.json  exact content, as JSON",
                           formatter_class=WrapHelp)
    cl.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                    help="only this service's translations")
    cl.add_argument("-t", "--target", metavar="CODE", help="only this target language, e.g. PT-BR")
    cl.add_argument("--lang", metavar="CODE", help="only this source language, e.g. IT")
    cl.add_argument("--text", metavar="TEXT", help="only original texts containing TEXT (any case)")
    cl.add_argument("-n", "--limit", type=int, metavar="N", help="max rows (default: all)")
    cl.add_argument("-o", "--output", metavar="FILE",
                    help="write everything to FILE instead of the screen (.json = JSON, "
                         "otherwise readable text; asks before replacing one)")
    cl.add_argument("--json", action="store_true", help="JSON instead of the readable list")
    cl.add_argument("--no-pager", action="store_true",
                    help="print straight to the terminal instead of the scrollable viewer")
    ci = ca_sub.add_parser("invalidate", parents=[common, cdir],
                           help="delete saved translations (by service, language or text), or everything",
                           description="Deletes the saved translations that match ALL the "
                                       "filters given. Text languages are kept. Asks first unless -y. "
                                       "Texts deleted this way are sent to the service again next time.",
                           epilog="examples:\n"
                                  "  ankitool cache invalidate -p gemini              all of Gemini's\n"
                                  "  ankitool cache invalidate -p gemini -t PT-BR --lang IT\n"
                                  "  ankitool cache invalidate --text \"ciao\"          one text, all services\n"
                                  "  ankitool cache invalidate --all -y               empty the cache",
                           formatter_class=WrapHelp)
    ci.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                    help="only this service's translations")
    ci.add_argument("-t", "--target", metavar="CODE", help="only this target language, e.g. PT-BR")
    ci.add_argument("--lang", metavar="CODE", help="only this source language, e.g. IT")
    ci.add_argument("--text", metavar="TEXT", help="only this original text")
    ci.add_argument("--all", action="store_true",
                    help="delete all translations and text languages (manual corrections are kept)")
    cu = ca_sub.add_parser("use", parents=[common, cdir],
                           help="set (or show) one service's mode: shared, provider or isolated",
                           description="Sets the reuse mode of one service, saved in config.json. "
                                       "Without MODE it shows the current one. --reset goes back to shared.",
                           epilog="examples:\n"
                                  "  ankitool cache use deepl isolated\n"
                                  "  ankitool cache use gemini\n"
                                  "  ankitool cache use gemini --reset",
                           formatter_class=WrapHelp)
    cu.add_argument("service", nargs="?", metavar="SERVICE", choices=sorted(PROVIDERS),
                    help="translation service, e.g. deepl, azure, gemini, groq, openai")
    cu.add_argument("mode", nargs="?", metavar="MODE", choices=list(MODES),
                    help="shared, provider or isolated (omit to show the current mode)")
    cu.add_argument("--reset", action="store_true", help="remove the saved mode (back to shared)")

    c = sub.add_parser("config", parents=[common],
                       help="show or change the saved preferences (config.json)",
                       description="Saved preferences in config.json: the default service, and per-service "
                                   "model, base_url, max_workers and cache mode. API keys are never stored "
                                   "here (they stay in .env or environment variables). Lookup order: "
                                   "command-line flag, then environment/.env, then config.json, then the "
                                   "built-in default. With no action it shows the settings.",
                       epilog="keys:\n"
                              "  provider                       default service (deepl, azure, gemini, groq, openai)\n"
                              "  SERVICE.model                  model name for that service\n"
                              "  SERVICE.base_url               API address (https only, except localhost)\n"
                              "  SERVICE.max_workers            parallel requests (whole number >= 1)\n"
                              "  SERVICE.rpm                    max requests per minute (whole number >= 1)\n"
                              "  SERVICE.cache                  cache mode: shared, provider or isolated\n"
                              "examples:\n"
                              "  ankitool config show\n"
                              "  ankitool config set provider groq\n"
                              "  ankitool config set gemini.cache isolated\n"
                              "  ankitool config unset provider",
                       formatter_class=WrapHelp)
    c.add_argument("action", nargs="?", choices=["show", "set", "unset"], default="show",
                   help="what to do: show (default), set, or unset a preference")
    c.add_argument("key", nargs="?", metavar="KEY",
                   help="provider, or SERVICE.model / SERVICE.base_url / SERVICE.max_workers / SERVICE.rpm / SERVICE.cache")
    c.add_argument("value", nargs="?", metavar="VALUE", help="new value (for 'set')")

    opts = p.parse_args(argv)

    if opts.command == "audio":
        for attr, flag in (("only_fields", "--field"), ("skip_fields", "--skip-field")):
            specs = []
            for item in getattr(opts, attr) or []:
                nt, _, fld = item.rpartition(":")
                if not fld.strip():
                    a.error(f"{flag} invalid: '{item}'. Use FIELD or NOTETYPE:FIELD")
                specs.append((nt.strip() or None, fld.strip()))
            setattr(opts, attr, specs)
    elif opts.command == "translate":
        opts.deck_names = {}
        for item in opts.deck_name or []:
            old, eq, new = item.partition("=")
            old, new = old.strip(), new.strip()
            if not eq or not old or not new or SEP in old or SEP in new:
                t.error(f"invalid --deck-name: '{item}'. Use CURRENT=NEW (one level, no '{SEP}')")
            opts.deck_names[old] = new
        if opts.deck_names and opts.rename_decks == "none":
            t.error("--deck-name cannot be combined with --rename-decks none")
        if opts.deck_method == "keep" and not opts.deck_names:
            t.error("--deck-method keep requires at least one --deck-name")
        for attr, flag in (("only_fields", "--field"), ("skip_fields", "--skip-field")):
            specs = []
            for item in getattr(opts, attr) or []:
                nt, _, fld = item.rpartition(":")
                if not fld.strip():
                    t.error(f"{flag} invalid: '{item}'. Use FIELD or NOTETYPE:FIELD")
                specs.append((nt.strip() or None, fld.strip()))
            setattr(opts, attr, specs)
    elif opts.command == "reverse":
        seen = set()
        for item in opts.swap or []:
            parts = [x.strip().lower() for x in re.split(r"<->|=", item)]
            if len(parts) != 2 or not all(parts) or parts[0] == parts[1] or seen & set(parts):
                r.error(f"--swap invalid: '{item}'. Use FIELD1=FIELD2, without repeating fields")
            seen.update(parts)
    elif opts.command == "config":
        if opts.action in ("set", "unset") and not opts.key:
            c.error(f"'config {opts.action}' needs a KEY: provider, or "
                    f"SERVICE.model / SERVICE.base_url / SERVICE.max_workers / SERVICE.rpm / SERVICE.cache")
        if opts.action == "set" and opts.value is None:
            c.error(f"'config set {opts.key}' needs a VALUE")
        if opts.action == "show" and opts.key:
            c.error("'config show' takes no KEY or VALUE")
    return opts