import sys

from ankitool.cli.args import parse_args
from ankitool.cli.console import Ui, choose_provider
from ankitool.cli.info import InfoApp
from ankitool.cli.reverse import ReverseApp
from ankitool.cli.translate import AnkiTranslatorApp
from ankitool.core.errors import AnkiToolError


MENU = {"1": ("translate", "Traduzir um baralho",
              "Troca o idioma do texto dos cartões (ex.: de italiano para português). Usa o DeepL."),
        "2": ("reverse", "Inverter os cartões (frente <-> verso)",
              "O que aparece na frente passa para o verso, e vice-versa. O texto não muda."),
        "3": ("info", "Ver o que tem dentro de um baralho",
              "Só mostra informações (tipos de nota, campos, baralhos). Não cria nenhum arquivo."),
        "0": ("exit", "Sair", None)}

BACK_TO_MENU = "\nQuer fazer mais alguma coisa?\n  (s = voltar ao menu principal, n = fechar o programa)"

def _failed(text, from_menu):
    """Mostra o erro. Devolve True se deve voltar ao menu; fora do menu, encerra com código 1."""
    print(f"\n{text}", file=sys.stdout if from_menu else sys.stderr)
    if not from_menu:
        sys.exit(1)
    return not Ui.auto and Ui.yes(BACK_TO_MENU, "s")

def main(argv=None):
    from_menu = False                                    # só volta ao menu quem começou por ele
    while True:
        try:
            opts = parse_args(argv)
            if getattr(opts, "command", None) is None:
                from_menu = True
                options = {k: (v[1], v[2]) for k, v in MENU.items()}
                key = Ui.choose("O que você quer fazer?", options, "1")
                if key == "0":
                    break
                opts = parse_args([MENU[key][0]])

            Ui.auto = getattr(opts, "yes", False)

            if opts.command == "reverse":
                ReverseApp(opts).run()
            elif opts.command == "info":
                InfoApp(opts).run()
            elif opts.command == "langs":
                provider = choose_provider(opts)
                if opts.source:
                    Ui.show_langs("Idiomas de origem que o serviço reconhece (código e nome):",
                                  provider.source_langs)
                else:
                    Ui.show_langs("Idiomas de saída disponíveis (código e nome):",
                                  provider.target_langs)
            elif opts.command != "exit":
                AnkiTranslatorApp(opts).run()

            if not from_menu or Ui.auto or not Ui.yes(BACK_TO_MENU, "s"):
                break
            argv = []  # Reseta os argumentos para forçar o menu interativo na próxima iteração

        except AnkiToolError as e:                       # erros esperados (nossos)
            if not _failed(f"[Aviso] {e}", from_menu):
                break
            argv = []
        except SystemExit:                               # só vem do argparse: -h, -V, erro de uso
            if not from_menu:
                raise
            if Ui.auto or not Ui.yes(BACK_TO_MENU, "s"):
                break
            argv = []
        except KeyboardInterrupt:
            print("\nInterrompido.")
            sys.exit(130)
        except Exception as e:
            if not _failed(f"[Erro Inesperado] {e}", from_menu):
                break
            argv = []
