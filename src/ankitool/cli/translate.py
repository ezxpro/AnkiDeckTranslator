from pathlib import Path

from ankitool.cache.translation import CacheView, DEFAULT_MODE, MODES, TranslationCache
from ankitool.core.settings import provider_option
from ankitool.cli.console import (ConsoleReporter, Ui, ask_input, ask_paths, choose_provider,
                                  dim, head, paint, pick_language)
from ankitool.cli.rename import DeckRenameCli
from ankitool.core.errors import AnkiToolError, Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.ops.rename import DeckRenamer, RenameConfig
from ankitool.ops.translate import TranslateConfig, TranslateOperation


def show_detected(op, detected):
    """The language found in each field (shared by 'translate' and 'audio'); op has .anki and .cfg."""
    print("\n=== Language I found in each field ===")
    print("(I looked at some cards of each field and guessed the language. "
          "The % shows how sure I am.)")
    cur = None
    for (mid, f), (lang, ratio, ex) in sorted(detected.items(), key=lambda kv: kv[0][0]):
        if mid != cur:
            print(f"\n{op.anki.model_names[mid]}")
            cur = mid
        ok = ratio >= op.cfg.min_ratio
        label = paint(lang, "1;32") if ok else paint(f"{lang}? (uncertain)", "33")
        pct = dim(f"({ratio:.0%})")
        print(f" {f}: {label} {pct} e.g.: {dim(ex)}")


class AnkiToolApp:
    """Coordinates the flow: questions -> import -> detect -> translate -> rename -> export."""

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
            self.rep.log(f"current usage {self.client.label}:", usage)
        # mode: --cache-mode > providers.NAME.cache in config.json > shared
        self.mode = opts.cache_mode or provider_option(self.client.name, "cache") or DEFAULT_MODE
        if self.mode not in MODES:
            raise ConfigError(f"invalid cache mode '{self.mode}' for {self.client.name}. Use: {', '.join(MODES)}")
        self.cache = TranslationCache(self.cache_dir / "cache.sqlite", self.rep)
        self.rep.log(f"cache (cache.sqlite): {self.cache.count()} translations, "
                     f"{len(self.cache.lang)} text languages; mode: {self.mode} for {self.client.name}")
        self.target = None

    def _accept_target(self, target):
        """False if the user declines a language that is not in the service's list (LLMs take any name)."""
        if target in self.client.target_codes:
            return True
        msg = f"'{target}' is not in the service's list; the model will translate into it as typed."
        if Ui.auto or self.opts.target:
            print(f"  {msg}")
            return True
        return Ui.yes(f"  {msg} Continue?", "n")

    # ---- questions ----
    def ask_target(self):
        if self.opts.target:
            target = self.client.resolve_target(self.opts.target)
            if not target:
                raise ConfigError(f"Invalid target language: {self.opts.target}. "
                                  f"Use the 'langs' command to see the codes.")
            self._accept_target(target)
        else:
            default = self.client.default_target
            title = "Translate the deck into which language?"
            raw = pick_language(title, self.client.target_langs, default)
            while True:
                if raw is None:                          # no interactive terminal: plain question
                    if not Ui.auto:
                        Ui.show_langs("Languages you can translate into (code and name):",
                                      self.client.target_langs)
                        print(dim(f"Type a code or a name (e.g. PT-BR, french). Enter = {default}."))
                    raw = Ui.ask("Target language", default)
                target = self.client.resolve_target(raw)
                if target and self._accept_target(target):
                    break
                if not target:
                    print(paint(f"  '{raw}' is not a valid language. Type a code or a language name.", "31"))
                raw = pick_language(title, self.client.target_langs, default)
        self.target = target

    def show_detected(self, op, detected):
        show_detected(op, detected)

    def ask_languages(self, op, detected):
        options, already = op.language_options(detected)
        if not options:
            raise AnkiToolError("\nNo field in another language to translate.")

        labels = [f"{code} - {self.client.source_names.get(code, code)} ({c} fields)"
                  for code, c in options]
        note = (f"({already} field(s) are already in {op.tbase} and will be kept as they are; "
                f"'uncertain' fields too.)") if already else None

        if self.opts.lang or Ui.auto:                    # no question: --lang, or all with --yes
            print(f"\n=== Languages that can be translated into {self.target} ===")
            for i, lab in enumerate(labels, 1):
                print(f" {i}) {lab}")
            if note:
                print(f" {note}")
            return TranslateOperation.validate_languages(self.opts.lang, dict(options))

        intro = ("These are the languages I found in the deck. Choose which ones you want to translate."
                 + (f"\n{note}" if note else ""))
        idx = Ui.select(f"Which languages do you want to translate into {self.target}?", labels,
                        multi=True, default="all" if len(options) == 1 else None,
                        intro=intro,
                        hint="You can also type the language code (e.g. IT, or IT,EN).",
                        aliases={code.upper(): i for i, (code, _) in enumerate(options)})
        if idx is None:
            raise Cancelled("Cancelled.")
        return {options[i][0] for i in idx}

    def print_estimate(self, est):
        print("\n=== Simulation (--dry-run) ===")
        print(f"Texts that would be sent: {est.texts}")
        print(f"Estimated characters: ~{est.chars}")
        print(f"Already in cache (no cost): {est.cached}")
        for (L, T), n in est.by_pair.items():
            print(f"  {L} -> {T}: {n} texts")

    # ---- flow ----
    def run(self):
        opts, dry = self.opts, self.opts.dry_run
        src, out = (ask_input(opts), None) if dry else ask_paths(opts)   # dry: no file is created
        self.ask_target()

        cfg = TranslateConfig(
            target=self.target, sample=opts.sample, min_ratio=opts.min_ratio,
            only_fields=opts.only_fields or [], skip_fields=opts.skip_fields or [],
            keep_scheduling=opts.keep_scheduling, legacy=opts.legacy, cache_mode=self.mode)
        op = TranslateOperation(self.client, self.cache, cfg, self.rep)
        try:
            op.open(src)
            detected = op.detect()
            self.show_detected(op, detected)

            picked = self.ask_languages(op, detected)
            chosen = op.plan(detected, picked)
            self.rep.log("chosen languages:", ", ".join(sorted(picked)), f"-> {len(chosen)} fields")

            est = op.translate(chosen, dry_run=dry)
            if dry:
                self.print_estimate(est)
                usage = self.client.usage()
                if usage is not None:
                    print(f"Current usage of {self.client.label}: {usage}")
                print("(Language detection has already spent some characters.)")
                print("Nothing was translated or exported (--dry-run).")
                return
            op.apply(chosen)

            src_lang = next(iter(picked)) if len(picked) == 1 else None
            view = CacheView(self.cache, self.client.name, self.client.variant, self.mode)
            renamer = DeckRenamer(op.anki, self.client, self.target, view, self.rep)
            DeckRenameCli(renamer, RenameConfig(opts.rename_decks, opts.deck_method,
                                                opts.deck_names)).run(src_lang)

            op.export(out)
            print("done ->", out)
            usage = self.client.usage()
            if usage is not None:
                self.rep.log(f"{self.client.label} usage at the end:", usage)
        finally:
            op.close()