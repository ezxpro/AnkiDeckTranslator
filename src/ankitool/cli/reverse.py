from ankitool.anki.collection import AnkiCollection
from ankitool.cli.console import ConsoleReporter, Ui, ask_paths
from ankitool.core.errors import AnkiToolError
from ankitool.ops.reverse import CardReverser, ReverseConfig


class ReverseCli:
    """Perguntas e textos do comando 'reverse'. Toda a lógica está em CardReverser."""

    def __init__(self, rev, cfg):
        self.rev, self.cfg = rev, cfg

    def pick_notetypes(self, cands):
        if self.cfg.notetypes:
            return self.rev.select_by_name(cands, self.cfg.notetypes)
        if Ui.auto:
            return cands
        labels = [f"{nt['name']}  ({self.rev.counts[nt['id']]} notas, {len(nt['flds'])} campos)"
                  for nt in cands]
        idx = Ui.select(
            "Quais tipos de nota você quer inverter?", labels, multi=True, default="all",
            intro=("Um 'tipo de nota' é o modelo dos seus cartões (por exemplo: Básico, Vocabulário).\n"
                   "Cada tipo tem 'campos', como Palavra e Tradução. Os números entre parênteses\n"
                   "dizem quantas notas usam esse tipo."))
        return [] if idx is None else [cands[i] for i in idx]

    def show_layout(self, nt, pairs):
        front, back = CardReverser.layout(nt)
        paired = {n for p in pairs for n in p}
        stay = [f["name"] for f in nt["flds"] if f["name"] not in paired]
        print(f"\n Tipo de nota: {nt['name']} ({self.rev.counts[nt['id']]} notas)")
        print(f"   Hoje a FRENTE do cartão mostra: {', '.join(front) or '-'}")
        print(f"   Hoje o VERSO do cartão mostra:  {', '.join(back) or '-'}")
        print("   Para inverter, eu trocaria de lugar estes campos:")
        for a, b in pairs:
            print(f"     {a}  <->  {b}")
        if stay:
            print(f"   Estes não mudam de lado: {', '.join(stay)}")

    def pick_pairs(self, nt):
        names = [f["name"] for f in nt["flds"]]
        if self.cfg.swap:
            return self.rev.pairs_for(nt, self.cfg.swap)
        pairs = self.rev.suggest_pairs(nt)
        if Ui.auto or len(names) == 2:                    # com 2 campos não há o que escolher
            return pairs
        self.show_layout(nt, pairs)
        key = Ui.choose(
            f"O que fazer com o tipo '{nt['name']}'?",
            {"1": ("Usar a troca sugerida acima", "É o que eu recomendo na maioria dos casos."),
             "2": ("Digitar outros pares de campos",
                   "Você diz quais campos trocam de lugar."),
             "0": ("Pular este tipo de nota", "Ele não será invertido.")}, "1")
        if key == "1":
            return pairs
        if key == "0":
            return None
        print("\nDigite os pares assim: Campo1=Campo2 (para vários, separe por vírgula).")
        print(f"Exemplo: {names[0]}={names[1]}")
        print(f"Campos deste tipo: {', '.join(names)}")
        while True:
            raw = input("Pares (ou 0 para pular este tipo): ").strip()
            if raw == "0":
                return None
            custom = CardReverser.parse_pairs(raw, names)
            if custom:
                return custom
            print(f"  Pares inválidos. Use só nomes de campos deste tipo: {', '.join(names)}")

    def run(self):
        """Devolve quantos tipos de nota foram invertidos."""
        cands = self.rev.eligible()
        if not cands:
            self.rev.rep.log("nenhum tipo de nota com frente e verso para inverter")
            return 0
        plan = []
        for nt in self.pick_notetypes(cands):
            pairs = self.pick_pairs(nt)
            if pairs:
                plan.append((nt, pairs))
        if not plan:
            return 0
        print("\n=== Resumo: o que vai ser invertido ===")
        for nt, pairs in plan:
            print(f" {nt['name']} ({self.rev.counts[nt['id']]} notas, "
                  f"{len(nt['tmpls'])} modelo(s) de cartão):")
            for a, b in pairs:
                print(f"   {a}  <->  {b}")
        if not Ui.yes("\nQuer inverter esses cartões?", "s"):
            return 0
        return self.rev.apply(plan)

class ReverseApp:
    """Comando reverse: importar -> inverter cartões -> exportar. Não usa serviço de tradução."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes

    def run(self):
        if not Ui.auto:
            print("\n=== Inverter os cartões ===")
            print("O que hoje aparece na FRENTE do cartão passa para o VERSO, e vice-versa.")
            print("O texto das suas notas não muda: só muda o jeito de mostrar.")
            print("Se rodar de novo no arquivo novo, tudo volta ao que era.")
        src, out = ask_paths(self.opts, suffix="_reversed")
        rep = ConsoleReporter(quiet=self.opts.quiet)
        anki = AnkiCollection(src, self.opts.keep_scheduling, self.opts.legacy, rep)
        try:
            anki.import_package()
            if not ReverseCli(CardReverser(anki, rep),
                             ReverseConfig(self.opts.notetype, self.opts.swap)).run():
                raise AnkiToolError("Nada foi invertido; nenhum arquivo gerado.")
            anki.export(out)
            print("pronto ->", out)
        finally:
            anki.close()
