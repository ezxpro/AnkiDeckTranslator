from ankitool.cli.console import Ui
from ankitool.core.constants import SEP
from ankitool.ops.rename import DeckRenamer


class DeckRenameCli:
    """Questions and texts for deck renaming. All the logic is in DeckRenamer."""

    def __init__(self, ren, cfg):
        self.ren, self.cfg = ren, cfg

    def pick_scope(self, paths, mode):
        """Returns the set of prefixes (tuples of segments) whose name will be translated."""
        scope = {"main": "1", "all": "2"}.get(mode) or Ui.choose(
            "Which decks do you want to rename?",
            {"1": ("Only the top-level decks",
                   "Decks INSIDE another deck keep their current name."),
             "2": ("All decks",
                   "The top-level decks and the ones inside them."),
             "3": ("I'll choose from a list",
                   "You see the list and type the numbers of the decks you want to rename."),
             "0": ("Cancel", "Don't change the deck names.")}, "2")
        if scope == "0":
            return None
        if scope in ("1", "2"):
            return DeckRenamer.scope_for("main" if scope == "1" else "all", paths)
        labels = ["  " * (len(p) - 1) + p[-1] for p in paths]
        idx = Ui.select("Which decks do you want to rename?", labels, multi=True, default=None,
                        intro="(Decks further to the right are inside the deck above them.)")
        if idx is None:
            return None
        return {paths[k] for k in idx}               # only the last segment of each one changes

    def build_newseg(self, selected, method, src_lang):
        ren, overrides = self.ren, self.cfg.names    # --deck-name CURRENT=NEW: always wins
        if method in ("0", "1"):                     # no questions ("0": only the --deck-name ones)
            return ren.auto_newseg(selected, overrides, src_lang, translate=(method == "1"))
        auto = {}
        if method == "3":
            auto = ren.auto_names({p[-1] for p in selected if p[-1] not in overrides}, src_lang)
        if not Ui.auto:
            print("\nI'll show one deck at a time.")
            print("The name in [brackets] is the one used if you just press Enter.")
            print("To use another name, type the new name and press Enter.\n")
        newseg, memo = {}, {}
        for p in sorted(selected, key=lambda p: tuple(s.lower() for s in p)):
            seg = p[-1]
            if seg in overrides:
                new = overrides[seg]
            else:
                suggestion = memo.get(seg) or (auto.get(seg, seg) if method == "3" else seg)
                while True:
                    new = Ui.ask(f"  Deck '{SEP.join(p)}' - new name", suggestion)
                    if new and SEP not in new:
                        break
                    print(f"  Name is empty or contains '{SEP}', which is not valid. Try another.")
                memo[seg] = new                       # same segment -> same suggestion
            if new != seg:
                newseg[p] = new
        return newseg

    def run(self, src_lang=None):
        ren, cfg = self.ren, self.cfg
        # None = ask; none/main/all. --deck-name alone implies the "all" scope
        mode = cfg.mode or ("all" if cfg.names else None)
        decks = ren.anki.deck_list()
        if mode == "none" or not decks:
            return
        if mode is None and not Ui.yes(
                "\nDo you also want to change the deck NAMES?\n"
                "  (This is only the names of the folders where the cards are kept in Anki. "
                "The cards have already been translated.)", "n"):
            return
        selected = self.pick_scope(ren.paths(decks), mode)
        if not selected:
            return
        # method fixed by option; if the scope came from an option, the default is automatic
        fixed_method = {"auto": "1", "manual": "2", "review": "3", "keep": "0"}.get(
            cfg.method) or ("0" if cfg.names else "1" if mode else None)

        while True:
            method = fixed_method or Ui.choose(
                "How do you want to choose the new names?",
                {"1": (f"Automatic ({ren.client.label})",
                       f"{ren.client.label} translates all the names by itself. You only check them at the end."),
                 "2": ("Manual",
                       "You type the new name of each deck. Enter keeps the current name."),
                 "3": ("Review one by one",
                       f"{ren.client.label} suggests a name for each deck. "
                       "Enter accepts the suggestion; or you type another.")}, "1")
            plan = ren.make_plan(decks, self.build_newseg(selected, method, src_lang))
            if not plan:
                ren.rep.log("no deck name changed")
                return

            print("\n=== Deck names: before  ->  after ===")
            for old, new in sorted(plan.values()):
                print(f" {old}  ->  {new}")

            dups = ren.duplicates(decks, plan)
            if dups:
                print("\nProblem: more than one deck would end up with the same name:")
                for k in dups:
                    print(f" {k}")
                if fixed_method or not Ui.yes("\nDo you want to try again, choosing other names?", "y"):
                    return                               # with a fixed method, the same conflict would happen
                continue
            if Ui.yes("\nDo you want to apply these new names?", "y"):
                break
            if not Ui.yes("\nDo you want to try again, choosing other names?", "n"):
                return

        ren.apply(plan)
