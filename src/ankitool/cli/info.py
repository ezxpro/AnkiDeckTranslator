from ankitool.anki.collection import AnkiCollection
from ankitool.cli.console import ConsoleReporter, ask_input
from ankitool.core.constants import SEP
from ankitool.ops.reverse import CardReverser


class InfoApp:
    """Comando info: mostra tipos de nota, campos e baralhos do .apkg (não gera arquivo)."""

    def __init__(self, opts):
        self.opts = opts

    def run(self):
        anki = AnkiCollection(ask_input(self.opts), rep=ConsoleReporter(quiet=self.opts.quiet))
        try:
            anki.import_package()
            print("\n=== Tipos de nota (os modelos dos seus cartões) ===")
            for nt in sorted(anki.notetypes(), key=lambda nt: nt["name"].lower()):
                n = anki.note_count(nt["id"])
                if n == 0:
                    continue                             # tipos padrão do Anki sem notas
                kind = "cloze" if nt["type"] == 1 else "padrão"
                print(f" {nt['name']} [{kind}] - {n} notas, {len(nt['tmpls'])} template(s)")
                print(f"   campos: {', '.join(f['name'] for f in nt['flds'])}")
                if nt["type"] == CardReverser.STANDARD and len(nt["flds"]) >= 2:
                    front, back = CardReverser.layout(nt)
                    print(f"   frente: {', '.join(front) or '-'}")
                    print(f"   verso:  {', '.join(back) or '-'}")
                    swap = ", ".join(f"{x}={y}" for x, y in CardReverser.suggest_pairs(nt))
                    print(f"   troca sugerida (--swap): {swap}")
            print("\n=== Baralhos ===")
            for _, name in sorted(anki.deck_list(), key=lambda d: d[1].lower()):
                parts = name.split(SEP)
                print(f" {'  ' * (len(parts) - 1)}{parts[-1]}")
        finally:
            anki.close()
