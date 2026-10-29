import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ankitool.cache.audio import AudioCache
from ankitool.cache.translation import TranslationCache
from ankitool.cli.console import (ConsoleReporter, Ui, ask_input, ask_paths, choose_provider, dim)
from ankitool.cli.translate import show_detected
from ankitool.core.errors import AnkiToolError, Cancelled, ConfigError, ProviderUnavailable
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.text import lang_base
from ankitool.ops.audio import AudioConfig, AudioOperation
from ankitool.tts.base import make_speech


def play(path):
    """Plays the file right here with a command-line player if one is installed (ffplay, mpg123,
    afplay); otherwise opens the system's default player. False if nothing could be started."""
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["mpg123", "-q"], ["afplay"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run([*cmd, str(path)], check=False)
                return True
            except OSError:
                pass
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False

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
        self._tmp, self._played = None, 0

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

    def choose_voices(self, op, speech, chosen):
        """Per language: use the default voice, or (optionally) hear voices and choose one.
        No questions with -y, --voice or --dry-run (the default voice is used).
        Returns {language: voice name} only for the languages where another voice was chosen."""
        if Ui.auto or self.opts.voice or self.opts.dry_run:
            return {}
        picks = {}
        for lang in sorted(set(chosen.values())):
            default = speech.resolve_voice(lang)
            if Ui.choose(f"Voice for {lang}", {
                    "1": f"Use the default voice ({default.name})",
                    "2": ("Hear and choose a voice", "Listen to samples before deciding.")}, "1") == "1":
                continue
            voices = speech.voices_for(lang)
            if not voices:
                print(dim(f"\n{speech.label} gave no voice list for {lang}: the default voice is used."))
                continue
            picks[lang] = self.pick_voice(op, voices, default, lang, op.sample_text(chosen, lang))
        return picks
    def pick_voice(self, op, voices, default, lang, deck_sample):
        print(f"\n=== Voice for {lang} ===")
        for i, v in enumerate(voices, 1):
            print(f" {i:>2}) {v.name:<34} {v.gender:<7}{'(default)' if v.name == default.name else ''}")
        print(dim("Enter = default | N = choose | ?N = hear voice N | ?N PHRASE = hear it once with "
                  "PHRASE | t PHRASE = use PHRASE as the sample | t = back to the deck's text"))
        sample = deck_sample
        while True:
            print(dim(f"Sample: {sample or '(none: type  t YOUR PHRASE)'}"))
            raw = input(f"Voice for {lang} [{default.name}]: ").strip()
            if not raw:
                return default.name
            if raw.isdigit():
                if 1 <= int(raw) <= len(voices):
                    return voices[int(raw) - 1].name
                print("  No such number.")
                continue
            m = re.fullmatch(r"\?\s*(\d+)(?:\s+(.+))?", raw, re.S)
            if m:
                n, text = int(m[1]), m[2] or sample
                if not 1 <= n <= len(voices):
                    print("  No such number.")
                elif not text:
                    print("  No sample yet: type  t YOUR PHRASE  first.")
                else:
                    self.hear(op, text, voices[n - 1])
                continue
            if raw.lower() == "t":
                sample = deck_sample
                continue
            m = re.fullmatch(r"t\s+(.+)", raw, re.I | re.S)
            if m:
                sample = m[1]                      # as typed: no cleanup, no length limit
                continue
            hit = next((v for v in voices if v.name.lower() == raw.lower()), None)
            if hit:
                return hit.name
            print("  Not understood.")

    def hear(self, op, text, voice):
        print(dim(f"  {voice.name}: {text}"))
        try:
            data = op.preview(text, voice)
        except (ProviderUnavailable, ConfigError) as e:
            print(f"  {e}")
            return
        self._tmp = self._tmp or tempfile.mkdtemp(prefix="ankitool_")
        self._played += 1
        path = Path(self._tmp) / f"preview{self._played}.{op.speech.extension}"
        path.write_bytes(data)
        if not play(path):
            print(f"  Could not start a player. Open this file yourself: {path}")
        elif self._played == 1:
            print(dim(f"  If you hear nothing, the file is {path}"))

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
            op.cfg.voices = self.choose_voices(op, speech, chosen)          
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
            if self._tmp:
                shutil.rmtree(self._tmp, ignore_errors=True)
