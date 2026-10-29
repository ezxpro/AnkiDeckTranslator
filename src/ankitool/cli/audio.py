from pathlib import Path

from ankitool.cache.audio import AudioCache
from ankitool.cache.translation import TranslationCache
from ankitool.cli.console import (ConsoleReporter, Ui, ask_input, ask_paths, choose_provider, dim)
from ankitool.cli.translate import show_detected
from ankitool.core.errors import AnkiToolError, Cancelled, ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.text import lang_base
from ankitool.ops.audio import AudioConfig, AudioOperation
from ankitool.tts.base import make_speech


class AudioApp:
    """audio command: import -> detect languages -> choose -> synthesize -> add to the notes -> export.
    Only notes with no [sound:] in any field are touched."""

    def __init__(self, opts):
        self.opts = opts
        Ui.auto = opts.yes
        self.rep = ConsoleReporter(quiet=opts.quiet)
        self.cache_dir = (Path(opts.cache_dir).expanduser().resolve()
                          if opts.cache_dir else SCRIPT_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ask_languages(self, op, detected):
        options = op.language_options(detected)
        if not options:
            raise AnkiToolError("\nNo field with a language I could identify.")
        valid = dict(options)
        if self.opts.lang:
            return op.validate_languages(self.opts.lang, valid)
        if Ui.auto:
            raise ConfigError("With -y/--yes give the language to voice with --lang "
                              "(all detected languages would include your own).")
        labels = [f"{code} ({n} fields)" for code, n in options]
        idx = Ui.select("Which languages should be read aloud?", labels, multi=True, default=None,
                        intro="Choose the language you are LEARNING, not your own.",
                        hint="You can also type the language code (e.g. IT).",
                        aliases={code.upper(): i for i, (code, _) in enumerate(options)})
        if idx is None:
            raise Cancelled("Cancelled.")
        return {options[i][0] for i in idx}

    def print_estimate(self, est, speech):
        print("\n=== Audio to generate ===")
        print(f"Notes in the deck: {est.notes}")
        print(f"  skipped, already have audio: {est.with_audio}")
        if est.cloze:
            print(f"  skipped, cloze (the audio would give the answer away; --include-cloze): {est.cloze}")
        if est.too_long:
            print(f"  fields skipped, over the length limit (--max-chars): {est.too_long}")
        print(f"Fields that will get audio: {est.items}")
        for lang, n in sorted(est.by_lang.items()):
            print(f"  {lang}: {n} distinct texts")
        new = est.texts - est.cached
        print(f"Audio files: {est.texts} distinct, {est.cached} already generated, {new} to request "
              f"(~{est.chars} characters in total)")
        if new and speech.rpm:
            print(f"At {speech.rpm} requests/min and {speech.max_workers} worker(s): "
                  f"about {new / speech.rpm / max(speech.max_workers, 1):.0f} min.")

    def run(self):
        opts, dry = self.opts, self.opts.dry_run
        src, out = (ask_input(opts), None) if dry else ask_paths(opts, suffix="_audio")
        speech = make_speech(opts.speech)

        langs = {lang_base(t) for t in (opts.lang or "").replace(";", ",").split(",") if t.strip()}
        fixed = bool(langs and opts.only_fields)          # language and fields given: nothing to detect
        if opts.only_fields and not langs:
            print(dim("Note: --field limits the fields, but the language of each is still detected."))
        if fixed and len(langs) != 1:
            raise ConfigError("--lang with --field takes a single language.")
        client = cache = None
        if not fixed:
            print(dim("\nA translation service is used only to detect the language of each field."))
            client = choose_provider(opts)
            cache = TranslationCache(self.cache_dir / "cache.sqlite", self.rep)

        cfg = AudioConfig(voice=opts.voice, only_fields=opts.only_fields or [],
                          skip_fields=opts.skip_fields or [], sample=opts.sample,
                          min_ratio=opts.min_ratio, include_cloze=opts.include_cloze,
                          max_chars=opts.max_chars, keep_scheduling=opts.keep_scheduling,
                          legacy=opts.legacy)
        op = AudioOperation(speech, AudioCache(self.cache_dir / "audio"), cfg, self.rep, client, cache)
        try:
            op.open(src)
            if fixed:
                detected = op.assume(next(iter(langs)))
                picked = set(langs)
            else:
                detected = op.detect()
                show_detected(op, detected)
                picked = self.ask_languages(op, detected)
            chosen = op.plan(detected, picked)
            self.rep.log("chosen languages:", ", ".join(sorted(picked)), f"-> {len(chosen)} fields")

            est = op.prepare(chosen)
            self.print_estimate(est, speech)
            if dry:
                print("Nothing was generated or exported (--dry-run).")
                return
            if not est.items:
                raise AnkiToolError("Nothing to do: no field needs audio. No file created.")
            if not Ui.yes(f"\nGenerate the audio and add it to {est.items} fields?", "y"):
                raise Cancelled("Cancelled.")
            op.synthesize()
            op.apply()
            op.export(out)
            print("done ->", out)
            self.rep.log(f"{speech.label} usage:", speech.usage())
        finally:
            op.close()
