import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ankitool.cache.audio import AudioCache
from ankitool.cache.translation import TranslationCache
from ankitool.cli.console import (ConsoleReporter, Ui, _interactive, ask_input, ask_paths,
                                  choose_provider, dim, menu)
from ankitool.cli.translate import show_detected
from ankitool.core.errors import AnkiToolError, Cancelled, ConfigError, ProviderUnavailable
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.text import lang_base
from ankitool.ops.audio import AudioConfig, AudioOperation
from ankitool.tts.base import Delivery, make_speech


def _child_env():
    """Environment for external programs. The standalone binary points LD_LIBRARY_PATH
    at its own bundled libraries; players must not load those, so restore the user's original value"""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = orig
    return env

def play(path):
    """Plays the file right here with a command-line player if one is installed (ffplay, mpg123,
    afplay); otherwise opens the system's default player. False if nothing could be started."""
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["mpg123", "-q"], ["afplay"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run([*cmd, str(path)], check=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_child_env())
                return True
            except OSError:
                pass
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_child_env())
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_child_env())
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
        self.delivery = Delivery()
        self.styles = {}                      # language -> style chosen in the picker

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
        """Per language: pick a voice from a list where each one can be heard (Tab). Enter on the first
        item keeps the default voice. No questions with -y, --voice or --dry-run (default voice used).
        Returns {language: voice name} only for the languages where another voice was chosen;
        the style chosen for each voice that has them goes to self.styles."""
        if Ui.auto or self.opts.voice or self.opts.dry_run:
            return {}
        picks = {}
        for lang in sorted(set(chosen.values())):
            default = speech.resolve_voice(lang)
            voices = speech.voices_for(lang)
            if not voices:
                print(dim(f"\n{speech.label} returned no voice list for {lang}. Using the default voice "
                          f"({default.name}). To force another one: --voice NAME."))
                continue
            if all(v.name == default.name for v in voices):
                print(dim(f"\n{speech.label} has only one voice for {lang} ({default.name}). Using it."))
                continue
            sample = op.sample_text(chosen, lang)
            voice = self.pick_voice(op, voices, default, lang, sample)
            if voice.name != default.name:
                picks[lang] = voice.name
            style = self.pick_style(op, voice, lang, sample)
            if style is not None:
                self.styles[lang] = style
        return picks

    def pick_style(self, op, voice, lang, deck_sample):
        """Second menu for voices that have speaking styles; each can be heard (Tab). Skipped when
        --style was given (the flag wins). The default style (AZURE_SPEECH_STYLE) starts highlighted.
        Returns the style name, "" for no style, or None when there is nothing to choose."""
        if not voice.styles or self.opts.style is not None:
            return None
        labels = ["(no style)"] + list(voice.styles)
        by_name = {s.lower(): i for i, s in enumerate(labels)}
        pos = by_name.get(self.delivery.style.lower(), 0) if self.delivery.style else 0
        title = f"Style for {voice.name} ({lang})"
        if not _interactive():
            res = Ui.select(title, labels, multi=False, default="first")
            res = None if res is None else res[0]
        else:
            res = menu(title, labels, pos=pos, phrase="", phrase_default=deck_sample or "",
                       intro="Enter = use the highlighted style. Tab plays it. Same as --style NAME.",
                       on_tab=lambda i, text: self.hear(op, text, voice, "" if i == 0 else labels[i]))
        if res is None:
            raise Cancelled("Cancelled.")
        return "" if res == 0 else labels[res]

    def pick_voice(self, op, voices, default, lang, deck_sample):
        voices = [default] * (default.name not in {v.name for v in voices}) + list(voices)
        voices = sorted(voices, key=lambda v: v.name != default.name)      # default first, rest keep order
        labels = [f"{v.name}  {v.gender}" + ("  (default)" if v.name == default.name else "")
                  + ("  [HD]" if v.hd else "") + (f"  [styles: {len(v.styles)}]" if v.styles else "")
                  for v in voices]
        if not _interactive():                      # no keyboard menu available: numbered list, no preview
            res = Ui.select(f"Voice for {lang}", labels, multi=False, default="first")
            res = None if res is None else res[0]
        else:
            res = menu(f"Voice for {lang}", labels, phrase="", phrase_default=deck_sample or "",
                       intro="Enter = use the highlighted voice. Speed (--rate) works on every voice; "
                             "[styles] voices let you pick a speaking style next.",
                       on_tab=lambda i, text: self.hear(op, text, voices[i]))
        if res is None:
            raise Cancelled("Cancelled.")
        return voices[res]

    def hear(self, op, text, voice, style=None):
        """Plays `text` with `voice` (and `style`, if given). Returns an error message for the menu
        to show, or None."""
        try:
            data = op.preview(text, voice, style)
        except (ProviderUnavailable, ConfigError) as e:
            return str(e)
        self._tmp = self._tmp or tempfile.mkdtemp(prefix="ankitool_")
        self._played += 1
        path = Path(self._tmp) / f"preview{self._played}.{op.speech.extension}"
        path.write_bytes(data)
        if not play(path):
            return f"Could not start a player. Open this file yourself: {path}"
        return None

    def print_estimate(self, est, speech):
        print("\n=== Audio to generate ===")
        print(f"Notes in the deck: {est.notes}")
        print(f"  skipped, already have audio: {est.with_audio}")
        if est.cloze:
            print(f"  skipped, cloze (the audio would give the answer away; --include-cloze): {est.cloze}")
        if est.too_long:
            print(f"  fields skipped, over the length limit (--max-chars): {est.too_long}")
        print(f"Fields that will get audio: {est.items}")
        d = self.delivery
        if d.rate or d.style:
            print("Delivery: " + ", ".join(f"{k} {v}" for k, v in (("rate", d.rate), ("style", d.style)) if v))
        for lang, n in sorted(est.by_lang.items()):
            style = self.styles.get(lang)
            print(f"  {lang}: {n} distinct texts" + (f" (style: {style})" if style else ""))
        new = est.texts - est.cached
        print(f"Audio files: {est.texts} distinct, {est.cached} already generated, {new} to request "
              f"(~{est.chars} characters in total)")
        if new and speech.rpm:
            print(f"At {speech.rpm} requests/min and {speech.max_workers} worker(s): "
                  f"about {new / speech.rpm:.0f} min.")

    def run(self):
        opts, dry = self.opts, self.opts.dry_run
        src, out = (ask_input(opts), None) if dry else ask_paths(opts, suffix="_audio")
        speech = make_speech(opts.speech)
        base = speech.default_delivery()
        self.delivery = Delivery(opts.rate if opts.rate is not None else base.rate,
                                 opts.style if opts.style is not None else base.style)
        speech.variant(self.delivery)         # rejects a bad --rate now, before any picker or quota is used

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
                          legacy=opts.legacy,rate=self.delivery.rate, style=self.delivery.style)
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
            op.cfg.styles = dict(self.styles)
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