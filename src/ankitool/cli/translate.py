from pathlib import Path

from ankitool.cache.deckname import DeckNameCache
from ankitool.cache.translation import TranslationCache
from ankitool.cli.console import ConsoleReporter, Ui, ask_input, ask_paths, choose_provider
from ankitool.cli.rename import DeckRenameCli
from ankitool.core.errors import AnkiToolError, Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.ops.rename import DeckRenamer, RenameConfig
from ankitool.ops.translate import TranslateConfig, TranslateOperation


class AnkiTranslatorApp:
    """Orquestra o fluxo: perguntas -> importar -> detectar -> traduzir -> renomear -> exportar."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes
        self.rep = ConsoleReporter(quiet=opts.quiet)
        self.cache_dir = (Path(opts.cache_dir).expanduser().resolve()
                          if opts.cache_dir else SCRIPT_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = choose_provider(opts)
        usage = self.client.usage()
        if usage is not None:
            self.rep.log(f"uso atual {self.client.label}:", usage)
        self.cache = TranslationCache(self.client.cache_file("cache", self.cache_dir),
                                      self.client.target_codes, self.rep)
        self.deck_cache = DeckNameCache(self.client.cache_file("deck_cache", self.cache_dir), self.rep)
        self.rep.log(f"cache: {self.cache.count()} traduções e {len(self.cache.lang)} idiomas "
                     f"de texto guardados em {self.cache.path}")
        self.target = None

    # ---- perguntas ----
    def ask_target(self):
        if self.opts.target:
            target = self.client.resolve_target(self.opts.target)
            if not target:
                raise ConfigError(f"Idioma de saída inválido: {self.opts.target}. "
                                  f"Use o comando 'langs' para ver os códigos.")
        else:
            if not Ui.auto:
                Ui.show_langs("Idiomas para os quais dá para traduzir (código e nome):",
                              self.client.target_langs)
                print("Para qual idioma você quer traduzir o baralho?")
                print("Digite o CÓDIGO da lista acima (ex.: PT-BR, EN-US, ES) e aperte Enter.")
                print(f"Se você só apertar Enter, uso {self.client.default_target}.")
            while True:
                raw = Ui.ask("Idioma de saída", self.client.default_target)
                target = self.client.resolve_target(raw)
                if target:
                    break
                print(f"  '{raw}' não é um código da lista. Digite um dos códigos que aparecem acima.")
        self.target = target

    def show_detected(self, op, detected):
        print("\n=== Idioma que encontrei em cada campo ===")
        print("(Olhei alguns cartões de cada campo e adivinhei o idioma. "
              "A % diz o quanto tenho certeza.)")
        cur = None
        for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
            if mid != cur:
                print(f"\n{op.anki.model_names[mid]}")
                cur = mid
            label = lang if ratio >= op.cfg.min_ratio else f"{lang}? (incerto)"
            print(f" {f}: {label} ({ratio:.0%}) ex.: {ex}")

    def ask_languages(self, op, detected):
        options, already = op.language_options(detected)
        if not options:
            raise AnkiToolError("\nNenhum campo em outro idioma para traduzir.")

        labels = [f"{code} - {self.client.source_names.get(code, code)} ({c} campos)"
                  for code, c in options]
        note = (f"({already} campo(s) já estão em {op.tbase} e serão mantidos do jeito que estão; "
                f"campos 'incertos' também.)") if already else None

        if self.opts.lang or Ui.auto:                    # sem pergunta: --lang, ou todos com --yes
            print(f"\n=== Idiomas que podem ser traduzidos para {self.target} ===")
            for i, lab in enumerate(labels, 1):
                print(f" {i}) {lab}")
            if note:
                print(f" {note}")
            return TranslateOperation.validate_languages(self.opts.lang, dict(options))

        intro = ("Estes são os idiomas que encontrei no baralho. Escolha quais você quer traduzir."
                 + (f"\n{note}" if note else ""))
        idx = Ui.select(f"Quais idiomas você quer traduzir para {self.target}?", labels,
                        multi=True, default="all" if len(options) == 1 else None,
                        intro=intro,
                        hint="Também vale digitar o código do idioma (ex.: IT, ou IT,EN).",
                        aliases={code.upper(): i for i, (code, _) in enumerate(options)})
        if idx is None:
            raise Cancelled("Cancelado.")
        return {options[i][0] for i in idx}

    def print_estimate(self, est):
        print("\n=== Simulação (--dry-run) ===")
        print(f"Textos que seriam enviados: {est.texts}")
        print(f"Caracteres estimados: ~{est.chars}")
        print(f"Já no cache (sem custo): {est.cached}")
        for (L, T), n in est.by_pair.items():
            print(f"  {L} -> {T}: {n} textos")

    # ---- fluxo ----
    def run(self):
        opts, dry = self.opts, self.opts.dry_run
        src, out = (ask_input(opts), None) if dry else ask_paths(opts)   # dry: não gera arquivo
        self.ask_target()

        cfg = TranslateConfig(
            target=self.target, sample=opts.sample, min_ratio=opts.min_ratio,
            only_fields=opts.only_fields or [], skip_fields=opts.skip_fields or [],
            keep_scheduling=opts.keep_scheduling, legacy=opts.legacy)
        op = TranslateOperation(self.client, self.cache, cfg, self.rep)
        try:
            op.open(src)
            detected = op.detect()
            self.show_detected(op, detected)

            picked = self.ask_languages(op, detected)
            chosen = op.plan(detected, picked)
            self.rep.log("idiomas escolhidos:", ", ".join(sorted(picked)), f"-> {len(chosen)} campos")

            est = op.translate(chosen, dry_run=dry)
            if dry:
                self.print_estimate(est)
                usage = self.client.usage()
                if usage is not None:
                    print(f"Uso atual do {self.client.label}: {usage}")
                print("(A detecção de idioma já gastou alguns caracteres.)")
                print("Nada foi traduzido nem exportado (--dry-run).")
                return
            op.apply(chosen)

            src_lang = next(iter(picked)) if len(picked) == 1 else None
            renamer = DeckRenamer(op.anki, self.client, self.target, self.deck_cache, self.rep)
            DeckRenameCli(renamer, RenameConfig(opts.rename_decks, opts.deck_method,
                                                opts.deck_names)).run(src_lang)

            op.export(out)
            print("pronto ->", out)
            usage = self.client.usage()
            if usage is not None:
                self.rep.log(f"uso {self.client.label} ao final:", usage)
        finally:
            op.close()
