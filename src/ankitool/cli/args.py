from pathlib import Path
import argparse
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
        raise argparse.ArgumentTypeError(f"'{s}' não é um número inteiro maior que zero")
    return v

def ratio_type(s):
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        v = 0.0
    if not 0 < v <= 1:
        raise argparse.ArgumentTypeError(f"'{s}' não está entre 0 e 1 (ex.: 0.6)")
    return v

def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "-V", "--version"):
        argv.insert(0, "translate")                      # atalho: script deck.apkg -t PT-BR

    p = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Ferramentas para baralhos .apkg do Anki. Sem nenhum argumento, abre o "
                    "modo interativo (menu). Use 'COMANDO -h' para ver as opções de cada comando.",
        epilog="Em qualquer comando, o que não for informado por opção é perguntado, "
               "a menos que -y/--yes seja usado.")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMANDO")

    io = argparse.ArgumentParser(add_help=False)
    io.add_argument("input", nargs="?", metavar="ENTRADA", help="arquivo .apkg de entrada")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-y", "--yes", action="store_true",
                        help="não perguntar nada: usa os padrões de cada comando")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="só mostra avisos e o resultado final (sem mensagens de andamento)")
    run_opts = argparse.ArgumentParser(add_help=False, parents=[common])
    run_opts.add_argument("-o", "--output", metavar="SAIDA", help="arquivo .apkg de saída")
    run_opts.add_argument("-f", "--force", action="store_true",
                          help="substitui o arquivo de saída se ele já existir "
                               "(sem isto, com -y o programa para com erro)")
    run_opts.add_argument("--keep-scheduling", action="store_true",
                          help="mantém o histórico de estudo (datas, intervalos) e as opções "
                               "dos baralhos no arquivo gerado (padrão: removidos)")
    run_opts.add_argument("--legacy", action=argparse.BooleanOptionalAction, default=True,
                          help="formato antigo do .apkg, aceito por qualquer versão do Anki/"
                               "AnkiDroid (padrão); --no-legacy gera o formato novo, menor")

    t = sub.add_parser(
        "translate", parents=[io, run_opts],
        help="traduz os campos (e, opcionalmente, os nomes dos baralhos)",
        description="Traduz os campos de um baralho com um serviço de tradução (--provider). "
                    "Saída padrão: <entrada>_out.apkg. "
                    "Com -y: PT-BR, todos os idiomas detectados, nomes de baralhos intactos.")
    t.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICO",
                   help=f"serviço de tradução: {', '.join(sorted(PROVIDERS))} "
                        f"(padrão: {DEFAULT_PROVIDER})")
    t.add_argument("-t", "--target", metavar="CODIGO",
                   help="idioma de saída, ex.: PT-BR, EN-US (veja o comando 'langs')")
    t.add_argument("-l", "--lang", metavar="CODIGOS",
                   help="idioma(s) de origem a traduzir, separados por vírgula "
                        "(ex.: IT,EN) ou 'all' para todos os detectados")
    t.add_argument("-d", "--rename-decks", choices=["none", "main", "all"],
                   help="nomes de baralhos: none = não alterar, main = só os baralhos "
                        "principais, all = principais e secundários")
    t.add_argument("-m", "--deck-method", choices=["auto", "manual", "review", "keep"],
                   help="como renomear baralhos: auto (pelo serviço), manual (você digita), "
                        "review (sugestão do serviço, um a um) ou keep (só os de --deck-name)")
    t.add_argument("--deck-name", action="append", metavar="ATUAL=NOVO",
                   help="renomeia o baralho de nome ATUAL (um nível, sem '::') para NOVO; "
                        "pode repetir. Sem -d/-m, só estes são renomeados")
    t.add_argument("--dry-run", action="store_true",
                   help="só calcula quantos textos e caracteres seriam traduzidos: não traduz "
                        "nem gera arquivo (a detecção de idioma ainda gasta alguns caracteres)")
    t.add_argument("--field", action="append", dest="only_fields", metavar="CAMPO",
                   help="traduz SÓ este campo (ou TIPO_DE_NOTA:CAMPO); pode repetir")
    t.add_argument("--skip-field", action="append", dest="skip_fields", metavar="CAMPO",
                   help="NÃO traduz este campo (ou TIPO_DE_NOTA:CAMPO); pode repetir")
    t.add_argument("--sample", type=positive_int, default=SAMPLE, metavar="N",
                   help=f"quantos textos de cada campo usar para detectar o idioma (padrão: {SAMPLE})")
    t.add_argument("--min-ratio", type=ratio_type, default=MIN_RATIO, metavar="R",
                   help=f"fração mínima (0 a 1) da amostra que precisa concordar no idioma "
                        f"para o campo ser considerado seguro (padrão: {MIN_RATIO})")
    t.add_argument("--cache-dir", metavar="PASTA",
                   help="pasta onde ficam os arquivos de cache (padrão: a pasta do script)")

    r = sub.add_parser(
        "reverse", parents=[io, run_opts],
        help="inverte frente e verso dos cartões (sem tradução)",
        description="Inverte frente e verso trocando os campos nos templates dos cartões. "
                    "Saída padrão: <entrada>_reversed.apkg. Rodar de novo desfaz. "
                    "Com -y: todos os tipos de nota (cloze não entra).")
    r.add_argument("--notetype", action="append", metavar="NOME",
                   help="inverte só este tipo de nota (pode repetir)")
    r.add_argument("--swap", action="append", metavar="CAMPO1=CAMPO2",
                   help="par de campos que trocam de lado; pode repetir (padrão: pares "
                        "sugeridos para cada tipo de nota; veja o comando 'info')")

    sub.add_parser("info", parents=[io, common],
                   help="mostra tipos de nota, campos e baralhos de um .apkg",
                   description="Mostra tipos de nota, campos e baralhos de um .apkg "
                               "(útil para escolher --notetype e --swap). Não gera arquivo.")
    lg = sub.add_parser("langs", parents=[common],
                        help="lista os idiomas do serviço de tradução (saída ou origem)")
    lg.add_argument("-p", "--provider", choices=sorted(PROVIDERS), metavar="SERVICO",
                    help=f"serviço de tradução (padrão: {DEFAULT_PROVIDER})")
    lg.add_argument("--source", action="store_true",
                    help="lista os idiomas de ORIGEM (os que o serviço reconhece) "
                         "em vez dos de saída")

    opts = p.parse_args(argv)

    if opts.command == "translate":
        opts.deck_names = {}
        for item in opts.deck_name or []:
            old, eq, new = item.partition("=")
            old, new = old.strip(), new.strip()
            if not eq or not old or not new or SEP in old or SEP in new:
                t.error(f"--deck-name inválido: '{item}'. Use ATUAL=NOVO (um nível, sem '{SEP}')")
            opts.deck_names[old] = new
        if opts.deck_names and opts.rename_decks == "none":
            t.error("--deck-name não combina com --rename-decks none")
        if opts.deck_method == "keep" and not opts.deck_names:
            t.error("--deck-method keep exige pelo menos um --deck-name")
        for attr, flag in (("only_fields", "--field"), ("skip_fields", "--skip-field")):
            specs = []
            for item in getattr(opts, attr) or []:
                nt, _, fld = item.rpartition(":")
                if not fld.strip():
                    t.error(f"{flag} inválido: '{item}'. Use CAMPO ou TIPO_DE_NOTA:CAMPO")
                specs.append((nt.strip() or None, fld.strip()))
            setattr(opts, attr, specs)
    elif opts.command == "reverse":
        seen = set()
        for item in opts.swap or []:
            parts = [x.strip().lower() for x in item.split("=")]
            if len(parts) != 2 or not all(parts) or parts[0] == parts[1] or seen & set(parts):
                r.error(f"--swap inválido: '{item}'. Use CAMPO1=CAMPO2, sem repetir campos")
            seen.update(parts)
    return opts
