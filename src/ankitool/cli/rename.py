from ankitool.cli.console import Ui
from ankitool.core.constants import SEP
from ankitool.ops.rename import DeckRenamer


class DeckRenameCli:
    """Perguntas e textos do renomeio de baralhos. Toda a lógica está em DeckRenamer."""

    def __init__(self, ren, cfg):
        self.ren, self.cfg = ren, cfg

    def pick_scope(self, paths, mode):
        """Devolve o conjunto de prefixos (tuplas de segmentos) cujo nome será traduzido."""
        scope = {"main": "1", "all": "2"}.get(mode) or Ui.choose(
            "Quais baralhos você quer renomear?",
            {"1": ("Só os baralhos principais",
                   "Os baralhos que ficam DENTRO de outro baralho mantêm o nome de hoje."),
             "2": ("Todos os baralhos",
                   "Os principais e os que ficam dentro deles."),
             "3": ("Eu escolho numa lista",
                   "Você vê a lista e digita os números dos baralhos que quer renomear."),
             "0": ("Cancelar", "Não mexer nos nomes dos baralhos.")}, "2")
        if scope == "0":
            return None
        if scope in ("1", "2"):
            return DeckRenamer.scope_for("main" if scope == "1" else "all", paths)
        labels = ["  " * (len(p) - 1) + p[-1] for p in paths]
        idx = Ui.select("Quais baralhos você quer renomear?", labels, multi=True, default=None,
                        intro="(Os baralhos mais para a direita ficam dentro do baralho de cima.)")
        if idx is None:
            return None
        return {paths[k] for k in idx}               # só o último segmento de cada um muda

    def build_newseg(self, selected, method, src_lang):
        ren, overrides = self.ren, self.cfg.names    # --deck-name ATUAL=NOVO: sempre vence
        if method in ("0", "1"):                     # sem perguntas ("0": só os de --deck-name)
            return ren.auto_newseg(selected, overrides, src_lang, translate=(method == "1"))
        auto = {}
        if method == "3":
            auto = ren.auto_names({p[-1] for p in selected if p[-1] not in overrides}, src_lang)
        if not Ui.auto:
            print("\nVou mostrar um baralho de cada vez.")
            print("O nome entre [colchetes] é o que será usado se você só apertar Enter.")
            print("Para usar outro nome, digite o nome novo e aperte Enter.\n")
        newseg, memo = {}, {}
        for p in sorted(selected, key=lambda p: tuple(s.lower() for s in p)):
            seg = p[-1]
            if seg in overrides:
                new = overrides[seg]
            else:
                suggestion = memo.get(seg) or (auto.get(seg, seg) if method == "3" else seg)
                while True:
                    new = Ui.ask(f"  Baralho '{SEP.join(p)}' - novo nome", suggestion)
                    if new and SEP not in new:
                        break
                    print(f"  Nome vazio ou contendo '{SEP}' não é válido. Tente outro.")
                memo[seg] = new                       # mesmo segmento -> mesma sugestão
            if new != seg:
                newseg[p] = new
        return newseg

    def run(self, src_lang=None):
        ren, cfg = self.ren, self.cfg
        # None = perguntar; none/main/all. --deck-name sozinho implica escopo "all"
        mode = cfg.mode or ("all" if cfg.names else None)
        decks = ren.anki.deck_list()
        if mode == "none" or not decks:
            return
        if mode is None and not Ui.yes(
                "\nQuer também mudar os NOMES dos baralhos?\n"
                "  (Isso é só o nome das pastas onde os cartões ficam guardados no Anki. "
                "Os cartões já foram traduzidos.)", "n"):
            return
        selected = self.pick_scope(ren.paths(decks), mode)
        if not selected:
            return
        # método fixo por opção; se o escopo veio por opção, o padrão é automático
        fixed_method = {"auto": "1", "manual": "2", "review": "3", "keep": "0"}.get(
            cfg.method) or ("0" if cfg.names else "1" if mode else None)

        while True:
            method = fixed_method or Ui.choose(
                "Como você quer escolher os nomes novos?",
                {"1": (f"Automático ({ren.client.label})",
                       f"O {ren.client.label} traduz todos os nomes sozinho. Você só confere no final."),
                 "2": ("Manual",
                       "Você digita o nome novo de cada baralho. Enter mantém o nome de hoje."),
                 "3": ("Revisar um a um",
                       f"O {ren.client.label} sugere um nome para cada baralho. "
                       "Enter aceita a sugestão; ou você digita outro.")}, "1")
            plan = ren.make_plan(decks, self.build_newseg(selected, method, src_lang))
            if not plan:
                ren.rep.log("nenhum nome de baralho mudou")
                return

            print("\n=== Nomes de baralhos: como era  ->  como vai ficar ===")
            for old, new in sorted(plan.values()):
                print(f" {old}  ->  {new}")

            dups = ren.duplicates(decks, plan)
            if dups:
                print("\nProblema: mais de um baralho ficaria com o mesmo nome:")
                for k in dups:
                    print(f" {k}")
                if fixed_method or not Ui.yes("\nQuer tentar de novo, escolhendo outros nomes?", "s"):
                    return                               # método fixo daria o mesmo conflito
                continue
            if Ui.yes("\nQuer aplicar esses nomes novos?", "s"):
                break
            if not Ui.yes("\nQuer tentar de novo, escolhendo outros nomes?", "n"):
                return

        ren.apply(plan)
