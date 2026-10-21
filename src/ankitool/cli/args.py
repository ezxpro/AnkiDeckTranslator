from pathlib import Path
import argparse
import re
import sys

from ankitool.core.constants import DEFAULT_PROVIDER, MIN_RATIO, SAMPLE, SEP, VERSION
from ankitool.providers.base import PROVIDERS


COMMANDS = ("translate", "reverse", "info", "langs")

def positive_int(s):
    try:
        v = int(s)
    except ValueError:
        v = 0
    if v < 1:
        raise argparse.ArgumentTypeError(f"'{s}' is not an integer greater than zero")
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
        argv.insert(0, "translate")                      # shortcut: script deck.apkg -t PT-BR

    p = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Tools for Anki .apkg decks. With no arguments, opens the "
                    "interactive mode (menu). Use 'COMMAND -h' to see each command's options.",
        epilog="For any command, whatever is not given as an option is asked for, "
               "unless -y/--yes is used.")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    io = argparse.ArgumentParser(add_help=False)
    io.add_argument("input", nargs="?", metavar="INPUT", help="input .apkg file")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-y", "--yes", action="store_true",
                        help="don't ask anything: use each command's defaults")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="only show warnings and the final result (no progress messages)")
    run_opts = argparse.ArgumentParser(add_help=False, parents=[common])
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
                        f"(default: {DEFAULT_PROVIDER})")
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

    r = sub.add_parser(
        "reverse", parents=[io, run_opts],
        help="swap front and back of cards (no translation)",
        description="Swaps front and back by exchanging the fields in the card templates. "
                    "Default output: <input>_reversed.apkg. Running it again undoes the change. "
                    "With -y: all note types (cloze is excluded).")
    r.add_argument("--notetype", action="append", metavar="NAME",
                   help="swap only this note type (can be repeated)")
    r.add_argument("--swap", action="append", metavar="FIELD1=FIELD2",
                   help="pair of fields that switch sides; can be repeated (default: pairs "
                        "suggested for each note type; see the 'info' command)")

    sub.add_parser("info", parents=[io, common],
                   help="shows note types, fields and decks of an .apkg",
                   description="Shows note types, fields and decks of an .apkg "
                               "(useful for choosing --notetype and --swap). Creates no file.")
    lg = sub.add_parser("langs", parents=[common],
                        help="list the translation service's languages (target or source)")
    lg.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICE",
                    help=f"translation service (default: {DEFAULT_PROVIDER})")
    lg.add_argument("--source", action="store_true",
                    help="list SOURCE languages (the ones the service recognizes) "
                         "instead of the target ones")

    opts = p.parse_args(argv)

    if opts.command == "translate":
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
    return opts
