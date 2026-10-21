from ankitool.anki.collection import AnkiCollection
from ankitool.cli.console import ConsoleReporter, Ui, ask_paths
from ankitool.core.errors import AnkiToolError
from ankitool.ops.reverse import CardReverser, ReverseConfig


class ReverseCli:
    """Questions and texts for the 'reverse' command. All the logic is in CardReverser."""

    def __init__(self, rev, cfg):
        self.rev, self.cfg = rev, cfg

    def pick_notetypes(self, cands):
        if self.cfg.notetypes:
            return self.rev.select_by_name(cands, self.cfg.notetypes)
        if Ui.auto:
            return cands
        labels = [f"{nt['name']}  ({self.rev.counts[nt['id']]} notes, {len(nt['flds'])} fields)"
                  for nt in cands]
        idx = Ui.select(
            "Which note types do you want to swap?", labels, multi=True, default="all",
            intro=("A 'note type' is the model of your cards (for example: Basic, Vocabulary).\n"
                   "Each type has 'fields', such as Word and Translation. The numbers in brackets\n"
                   "show how many notes use that type."))
        return [] if idx is None else [cands[i] for i in idx]

    def show_layout(self, nt, pairs):
        front, back = CardReverser.layout(nt)
        paired = {n for p in pairs for n in p}
        stay = [f["name"] for f in nt["flds"] if f["name"] not in paired]
        print(f"\n Note type: {nt['name']} ({self.rev.counts[nt['id']]} notes)")
        print(f"   Today the FRONT of the card shows: {', '.join(front) or '-'}")
        print(f"   Today the BACK of the card shows:  {', '.join(back) or '-'}")
        print("   To swap, I would exchange the places of these fields:")
        for a, b in pairs:
            print(f"     {a}  <->  {b}")
        if stay:
            print(f"   These fields don't change sides: {', '.join(stay)}")

    def pick_pairs(self, nt):
        names = [f["name"] for f in nt["flds"]]
        if self.cfg.swap:
            return self.rev.pairs_for(nt, self.cfg.swap)
        pairs = self.rev.suggest_pairs(nt)
        if Ui.auto or len(names) == 2:                    # with 2 fields there is nothing to choose
            return pairs
        self.show_layout(nt, pairs)
        key = Ui.choose(
            f"What do you want to do with the type '{nt['name']}'?",
            {"1": ("Use the suggested swap above", "This is what I recommend in most cases."),
             "2": ("Type other field pairs",
                   "You say which fields exchange places."),
             "0": ("Skip this note type", "It will not be swapped.")}, "1")
        if key == "1":
            return pairs
        if key == "0":
            return None
        print("\nType the pairs like this: Field1=Field2 (for several, separate them with commas).")
        print(f"Example: {names[0]}={names[1]}")
        print(f"Fields of this type: {', '.join(names)}")
        while True:
            raw = input("Pairs (or 0 to skip this type): ").strip()
            if raw == "0":
                return None
            custom = CardReverser.parse_pairs(raw, names)
            if custom:
                return custom
            print(f"  Invalid pairs. Use only field names of this type: {', '.join(names)}")

    def run(self):
        """Returns how many note types were swapped."""
        cands = self.rev.eligible()
        if not cands:
            self.rev.rep.log("no note type with a front and back to swap")
            return 0
        plan = []
        for nt in self.pick_notetypes(cands):
            pairs = self.pick_pairs(nt)
            if pairs:
                plan.append((nt, pairs))
        if not plan:
            return 0
        print("\n=== Summary: what will be swapped ===")
        for nt, pairs in plan:
            print(f" {nt['name']} ({self.rev.counts[nt['id']]} notes, "
                  f"{len(nt['tmpls'])} card template(s)):")
            for a, b in pairs:
                print(f"   {a}  <->  {b}")
        if not Ui.yes("\nDo you want to swap these cards?", "y"):
            return 0
        return self.rev.apply(plan)

class ReverseApp:
    """reverse command: import -> swap cards -> export. Does not use a translation service."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes

    def run(self):
        if not Ui.auto:
            print("\n=== Swap the cards ===")
            print("What is currently shown on the FRONT of the card moves to the BACK, and vice versa.")
            print("The text of your notes does not change: only the way it is shown.")
            print("If you run it again on the new file, everything goes back to how it was.")
        src, out = ask_paths(self.opts, suffix="_reversed")
        rep = ConsoleReporter(quiet=self.opts.quiet)
        anki = AnkiCollection(src, self.opts.keep_scheduling, self.opts.legacy, rep)
        try:
            anki.import_package()
            if not ReverseCli(CardReverser(anki, rep),
                             ReverseConfig(self.opts.notetype, self.opts.swap)).run():
                raise AnkiToolError("Nothing was swapped; no file created.")
            anki.export(out)
            print("done ->", out)
        finally:
            anki.close()
