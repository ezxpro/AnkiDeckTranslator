import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dataclasses import dataclass, field
import collections
import re

from ankitool.anki.collection import AnkiCollection
from ankitool.core.constants import AUDIO_MAX_CHARS as MAX_CHARS, MIN_RATIO, SAMPLE
from ankitool.core.errors import ConfigError, ProviderUnavailable
from ankitool.core.pool import run_chunks
from ankitool.core.progress import Reporter
from ankitool.core.text import TextTools, lang_base
from ankitool.ops.translate import FieldLanguageDetector


@dataclass
class AudioConfig:
    voice: str = None                                    # explicit voice name (needs a single language)
    voices: dict = field(default_factory=dict)           # language -> voice name (chosen in the picker)
    only_fields: list = field(default_factory=list)      # [(note type|None, field)]
    skip_fields: list = field(default_factory=list)
    sample: int = SAMPLE
    min_ratio: float = MIN_RATIO
    include_cloze: bool = False
    max_chars: int = MAX_CHARS
    keep_scheduling: bool = False
    legacy: bool = True


@dataclass
class AudioItem:
    note: object
    field: str
    value: str                                           # the field as it is now
    lang: str
    voice: object                                        # tts.base.Voice
    name: str                                            # media file name (see AudioCache.filename)
    text: str                                            # what the voice reads


@dataclass
class AudioEstimate:
    notes: int
    with_audio: int                                      # notes skipped: some field already has [sound:]
    cloze: int                                           # notes skipped: cloze (the audio would give the answer away)
    too_long: int                                        # fields skipped: over max_chars
    items: int                                           # fields that will get audio
    texts: int                                           # distinct audio files needed
    chars: int
    cached: int                                          # of those, already generated
    by_lang: dict = field(default_factory=dict)          # language -> distinct texts


class AudioOperation:
    """Adds spoken audio to the notes that have none, with no questions and no print/input:
    open -> detect -> plan -> synthesize -> apply -> export -> close.
    The audio is appended to the SAME field as the text it reads, as [sound:file.mp3], so it plays
    on whichever side of the card shows that field."""

    def __init__(self, speech, audio_cache, cfg, rep=None, client=None, cache=None):
        """client/cache (a translation service and the language cache) are only needed by detect()."""
        self.speech, self.audio, self.cfg = speech, audio_cache, cfg
        self.client, self.cache = client, cache
        self.rep = rep or Reporter()
        speech.on_wait = self.rep.waiting
        speech.cancel_event = self.rep.cancel.event
        self.anki = None
        self.items = []
        self._est = None

    def open(self, src):
        cfg = self.cfg
        self.anki = AnkiCollection(src, cfg.keep_scheduling, cfg.legacy, self.rep)
        self.anki.import_package()
        self.anki.load_notes()
        if cfg.only_fields or cfg.skip_fields:
            before = len(self.anki.field_values)
            for nt, fld in self.anki.filter_fields(cfg.only_fields, cfg.skip_fields):
                self.rep.warn(f"--field '{(nt + ':') if nt else ''}{fld}' does not match any field")
            self.rep.log(f"fields considered: {len(self.anki.field_values)} of {before} "
                         f"(by --field / --skip-field)")
            if not self.anki.field_values:
                raise ConfigError("No fields left after --field/--skip-field. "
                                  "Use the 'info' command to see the field names.")

    # ---- which fields are in which language ----
    def detect(self):
        """{(note type id, field): (language, share of the sample that agrees, example)}"""
        if self.client is None or self.cache is None:
            raise ConfigError("Language detection needs a translation service (-p) or --lang with --field.")
        return FieldLanguageDetector(self.client, self.cache, self.client.default_target, self.rep,
                                     sample=self.cfg.sample).detect(self.anki.field_values)

    def assume(self, lang):
        """Every considered field is in `lang` (when the person said so, no detection is needed)."""
        return {k: (lang_base(lang), 1.0, "") for k in self.anki.field_values}

    def language_options(self, detected):
        """[(language, number of fields)] that can be voiced, most fields first."""
        counts = collections.Counter(lang_base(l) for (l, r, _) in detected.values()
                                     if r >= self.cfg.min_ratio)
        return counts.most_common()

    @staticmethod
    def validate_languages(raw, valid):
        """'IT,EN' -> set of codes; ConfigError if any was not found in the deck's fields."""
        picked = {lang_base(t) for t in re.split(r"[,\s;]+", raw or "") if t}
        bad = sorted(picked - set(valid))
        if bad:
            raise ConfigError(f"Language(s) not detected in the fields: {', '.join(bad)}. "
                              f"Detected: {', '.join(valid) or 'none'}")
        return picked

    def plan(self, detected, picked):
        """{(note type id, field): language} for the fields to voice."""
        return {k: lang_base(l) for k, (l, r, _) in detected.items()
                if lang_base(l) in picked and r >= self.cfg.min_ratio}

    def sample_text(self, chosen, lang):
        """A typical-length real value of a chosen field in `lang`, to preview voices with."""
        texts = sorted({TextTools.speakable(v) for (mid, f), l in chosen.items() if l == lang
                        for v in self.anki.field_values.get((mid, f), [])}, key=len)
        texts = [t for t in texts if TextTools.has_letters(t) and len(t) <= 120]
        return texts[len(texts) // 2] if texts else ""

    def preview(self, text, voice):
        """Audio bytes of `voice` reading `text`. Stored in the audio cache under the normal name,
        so it is reused if the same text is generated later with the same voice."""
        name = self.audio.filename(self.speech.name, voice.name, text, self.speech.extension)
        if not self.audio.has(name):
            data = self.speech.synthesize(text, voice)
            if data is None:
                raise ProviderUnavailable(f"{self.speech.label} is unavailable; try again.")
            self.audio.write(name, data)
        return self.audio.read(name)

    # ---- what would be generated ----
    def prepare(self, chosen):
        """Lists the fields that will get audio and what it takes. Nothing is spent here."""
        cfg = self.cfg
        langs = sorted(set(chosen.values()))
        if cfg.voice and len(langs) > 1:
            raise ConfigError(f"--voice names one voice but {len(langs)} languages were chosen "
                              f"({', '.join(langs)}). Choose a single language.")
        voices = {l: self.speech.resolve_voice(l, cfg.voice or cfg.voices.get(l)) for l in langs}
        ext = self.speech.extension
        self.items, with_audio, cloze, too_long = [], 0, 0, 0
        for n in self.anki.notes:
            pairs = list(n.items())
            if any("[sound:" in v for _, v in pairs):
                with_audio += 1
                continue
            if n.mid in self.anki.cloze_mids and not cfg.include_cloze:
                cloze += 1
                continue
            for f, v in pairs:
                lang = chosen.get((n.mid, f))
                if not lang:
                    continue
                text = TextTools.speakable(v)
                if not TextTools.has_letters(text):
                    continue
                if len(text) > cfg.max_chars:
                    too_long += 1
                    continue
                voice = voices[lang]
                self.items.append(AudioItem(n, f, v, lang, voice, self.audio.filename(
                    self.speech.name, voice.name, text, ext), text))
        unique = {it.name: it for it in self.items}
        by_lang = collections.Counter(it.lang for it in unique.values())
        self._est = AudioEstimate(
            notes=len(self.anki.notes), with_audio=with_audio, cloze=cloze, too_long=too_long,
            items=len(self.items), texts=len(unique), chars=sum(len(it.text) for it in unique.values()),
            cached=sum(1 for name in unique if self.audio.has(name)), by_lang=dict(by_lang))
        return self._est

    # ---- generating ----
    def synthesize(self):
        """Generates the audio files that are not in the cache yet. Progress is kept on disk, so a
        failed or cancelled run continues from where it stopped."""
        todo = {it.name: it for it in self.items if not self.audio.has(it.name)}
        jobs, done, total = list(todo.values()), 0, len(todo)
        if not total:
            self.rep.log("all the audio is already generated (cache): nothing was sent to the service")
            return 0

        def work(it):
            return self.speech.synthesize(it.text, it.voice)

        def on_result(it, data):
            nonlocal done
            if data is None:
                raise ProviderUnavailable(f"{self.speech.label} is unavailable; run again "
                                          f"(the audio already generated is kept).")
            self.audio.write(it.name, data)
            done += 1
            self.rep.advance("synthesizing audio", done, total)

        self.rep.log(f"synthesizing {total} audio files with {self.speech.label} "
                     f"({self.speech.max_workers} worker(s)"
                     f"{f', {self.speech.rpm} requests/min' if self.speech.rpm else ''})...")
        run_chunks(work, jobs, self.speech.max_workers, self.rep, on_result)
        return total

    def apply(self):
        """Puts the files in the deck's media and appends [sound:...] to each field. Returns notes changed."""
        names = {}
        for it in self.items:
            if it.name not in names:
                names[it.name] = self.anki.add_media(it.name, self.audio.read(it.name))
        changed = {}
        for it in self.items:
            it.note[it.field] = f"{it.value} [sound:{names[it.name]}]"
            changed[id(it.note)] = it.note
        self.anki.update_notes(list(changed.values()))
        self.rep.log(f"notes updated: {len(changed)}")
        return len(changed)

    def export(self, out):
        self.anki.export(out)

    def close(self):
        if self.anki:
            self.anki.close()
