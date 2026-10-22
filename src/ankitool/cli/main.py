import sys

from ankitool.cli.args import parse_args
from ankitool.cli.bench import BenchApp
from ankitool.cli.config import ConfigApp
from ankitool.cli.console import Ui, choose_provider
from ankitool.cli.info import InfoApp
from ankitool.cli.reverse import ReverseApp
from ankitool.cli.translate import AnkiToolApp
from ankitool.core.errors import AnkiToolError


MENU = {"1": ("translate", "Translate a deck",
              "Changes the language of the card text (e.g. from Italian to Portuguese). Uses DeepL."),
        "2": ("reverse", "Swap the cards (front <-> back)",
              "What shows on the front moves to the back, and vice versa. The text does not change."),
        "3": ("info", "See what's inside a deck",
              "Only shows information (note types, fields, decks). Does not create any file."),
        "4": ("langs", "List the translation service's languages",
              "Shows which languages the service can translate into (and, with --source, detect)."),
        "5": ("config", "Show or change saved preferences",
              "The non-secret settings in config.json (provider, model, workers). No API keys."),
        "0": ("exit", "Quit", None)}

# Menu entries that need extra positional args beyond their command name.
MENU_ARGV = {"config": ["config", "show"]}

BACK_TO_MENU = "\nDo you want to do anything else?\n  (y = back to the main menu, n = quit the program)"

def _failed(text, from_menu):
    """Shows the error. Returns True if it should go back to the menu; outside the menu, exits with code 1."""
    print(f"\n{text}", file=sys.stdout if from_menu else sys.stderr)
    if not from_menu:
        sys.exit(1)
    return not Ui.auto and Ui.yes(BACK_TO_MENU, "y")

def main(argv=None):
    from_menu = False                                    # only those who started from the menu go back to it
    while True:
        try:
            opts = parse_args(argv)
            if getattr(opts, "command", None) is None:
                from_menu = True
                options = {k: (v[1], v[2]) for k, v in MENU.items()}
                key = Ui.choose("What do you want to do?", options, "1")
                if key == "0":
                    break
                opts = parse_args(MENU_ARGV.get(MENU[key][0], [MENU[key][0]]))

            Ui.auto = getattr(opts, "yes", False)

            if opts.command == "reverse":
                ReverseApp(opts).run()
            elif opts.command == "info":
                InfoApp(opts).run()
            elif opts.command == "bench":
                BenchApp(opts).run()
            elif opts.command == "config":
                ConfigApp(opts).run()
            elif opts.command == "langs":
                provider = choose_provider(opts)
                if getattr(opts, "source", False):
                    Ui.show_langs("Source languages the service recognizes (code and name):",
                                  provider.source_langs)
                else:
                    Ui.show_langs("Available target languages (code and name):",
                                  provider.target_langs)
            elif opts.command != "exit":
                AnkiToolApp(opts).run()

            if not from_menu or Ui.auto or not Ui.yes(BACK_TO_MENU, "y"):
                break
            argv = []  # Resets the arguments so the interactive menu shows up next time

        except AnkiToolError as e:                       # expected (our) errors
            if not _failed(f"[Warning] {e}", from_menu):
                break
            argv = []
        except SystemExit:                               # only from argparse: -h, -V, usage error
            if not from_menu:
                raise
            if Ui.auto or not Ui.yes(BACK_TO_MENU, "y"):
                break
            argv = []
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        except Exception as e:
            if not _failed(f"[Unexpected error] {e}", from_menu):
                break
            argv = []
