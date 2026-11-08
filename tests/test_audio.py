import io
import urllib.error
from unittest.mock import patch

import pytest

from ankitool.cache.audio import AudioCache
from ankitool.cli.args import parse_args
from ankitool.core.errors import ConfigError, ProviderUnavailable
from ankitool.core.text import TextTools
from ankitool.ops.audio import AudioConfig, AudioOperation
from ankitool.tts.azure import AzureSpeechProvider
from ankitool.tts.base import SPEECH, Delivery, Voice

AUDIO = b"ID3" + b"\x00" * 100                   # long enough to count as audio


# ---- text ----
def test_speakable_strips_markup_and_reads_cloze_answer():
    s = 'Il <b>gatto</b>&nbsp;mangia<br>il pesce [sound:a.mp3] {{c1::bene::hint}} &amp; <style>x{}</style>ok'
    assert TextTools.speakable(s) == "Il gatto mangia il pesce bene & ok"


def test_speakable_empty_when_only_markup():
    assert TextTools.speakable("<br>[sound:a.mp3] &nbsp;") == ""


# ---- audio cache ----
def test_audio_cache_name_is_stable_and_depends_on_voice(tmp_path):
    a = AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao")
    assert a == AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao")
    assert a != AudioCache.filename("azure", "it-IT-IsabellaNeural", "ciao")
    assert a.startswith("ankitool_") and a.endswith(".mp3")
    c = AudioCache(tmp_path / "audio")
    assert not c.has(a)
    c.write(a, AUDIO)
    assert c.has(a) and c.read(a) == AUDIO
    assert [p.name for p in (tmp_path / "audio").iterdir()] == [a]     # no .tmp left behind

def test_filename_variant_keeps_old_names_and_separates_deliveries():
    base = AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao")
    assert base == AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao", "mp3", "")
    slow = AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao", "mp3", "rate=-15%")
    assert slow != base
    assert slow != AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao", "mp3", "rate=-30%")
    assert slow == AudioCache.filename("azure", "it-IT-ElsaNeural", "ciao", "mp3", "rate=-15%")

# ---- Azure Speech ----
VOICES = [{"ShortName": "it-IT-ElsaNeural", "Locale": "it-IT", "Gender": "Female", "Status": "GA"},
          {"ShortName": "it-IT-DiegoNeural", "Locale": "it-IT", "Gender": "Male", "Status": "GA"},
          {"ShortName": "pt-BR-AntonioNeural", "Locale": "pt-BR", "Gender": "Male", "Status": "GA"},
          {"ShortName": "it-IT-OldNeural", "Locale": "it-IT", "Status": "Deprecated"}]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setattr("ankitool.core.settings.SCRIPT_DIR", tmp_path)       # no real .env
    for n in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_SPEECH_TIER", "AZURE_KEY", "AZURE_REGION"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("AZURE_SPEECH_REGION", "brazilsouth")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")


def _azure(script, voices=VOICES):
    """Real provider; only urlopen is replaced. script(request, call_number) -> bytes or raises."""
    calls = []

    def urlopen(req, timeout=None):
        if req.data is None:                                    # GET voices/list
            if voices is None:
                raise urllib.error.URLError("offline")
            import json
            return io.BytesIO(json.dumps(voices).encode())
        calls.append(req)
        return io.BytesIO(script(req, len(calls)))

    patcher = patch("ankitool.tts.azure.urllib.request.urlopen", urlopen)
    patcher.start()
    p = AzureSpeechProvider()
    p.rpm = None                                                # tests must not wait for the pacing
    return p, calls, patcher


def test_azure_registered_and_request_shape(clean_env):
    p, calls, patcher = _azure(lambda r, n: AUDIO)
    try:
        assert "azure" in SPEECH
        voice = p.resolve_voice("IT")
        assert voice.name == "it-IT-ElsaNeural"
        assert p.synthesize('Tom & "Jerry" <3', voice) == AUDIO
        req = calls[0]
        assert req.full_url == "https://brazilsouth.tts.speech.microsoft.com/cognitiveservices/v1"
        h = {k.lower(): v for k, v in req.header_items()}
        assert h["ocp-apim-subscription-key"] == "k"
        assert h["content-type"] == "application/ssml+xml"
        assert h["x-microsoft-outputformat"] == "audio-24khz-48kbitrate-mono-mp3"
        body = req.data.decode()
        assert "Tom &amp; \"Jerry\" &lt;3" in body and "xml:lang=\"it-IT\"" in body
        assert "name=\"it-IT-ElsaNeural\"" in body
        assert p.chars == len('Tom & "Jerry" <3')
    finally:
        patcher.stop()

def test_azure_delivery_in_ssml_and_variant(clean_env):
    p, calls, patcher = _azure(lambda r, n: AUDIO)
    try:
        voice = p.resolve_voice("IT")
        p.synthesize("ciao", voice)
        plain = calls[0].data.decode()
        assert "prosody" not in plain and "mstts" not in plain
        p.synthesize("ciao", voice, Delivery(rate="-15%", style="Friendly"))
        body = calls[1].data.decode()
        assert '<prosody rate="-15%">ciao</prosody>' in body
        assert '<mstts:express-as style="friendly">' in body and "xmlns:mstts=" in body
        assert p.variant(None) == "" and p.variant(Delivery()) == ""
        assert p.variant(Delivery("-15%", "Friendly")) == "rate=-15%;style=friendly"
        with pytest.raises(ConfigError):
            p.variant(Delivery(rate="sloooow"))
    finally:
        patcher.stop()

def test_azure_voice_markers_and_default_delivery(clean_env, monkeypatch):
    for n in ("AZURE_SPEECH_RATE", "AZURE_SPEECH_STYLE"):
        monkeypatch.delenv(n, raising=False)
    extra = VOICES + [
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female", "Status": "GA",
         "StyleList": ["chat", "cheerful"]},
        {"ShortName": "en-US-Ava:DragonHDLatestNeural", "Locale": "en-US", "Gender": "Female", "Status": "GA"}]
    p, _, patcher = _azure(lambda r, n: AUDIO, voices=extra)
    try:
        by = {v.name: v for v in p.voices()}
        assert by["en-US-JennyNeural"].styles == ("chat", "cheerful") and not by["en-US-JennyNeural"].hd
        assert by["en-US-Ava:DragonHDLatestNeural"].hd and by["en-US-Ava:DragonHDLatestNeural"].styles == ()
        assert by["it-IT-ElsaNeural"].styles == () and not by["it-IT-ElsaNeural"].hd
        assert p.default_delivery() == Delivery()
        monkeypatch.setenv("AZURE_SPEECH_RATE", "-10%")
        monkeypatch.setenv("AZURE_SPEECH_STYLE", "chat")
        assert p.default_delivery() == Delivery("-10%", "chat")
    finally:
        patcher.stop()

def test_voice_resolution(clean_env):
    p, _, patcher = _azure(lambda r, n: AUDIO)
    try:
        assert p.resolve_voice("pt").name == "pt-BR-AntonioNeural"      # preferred one is not in the list
        assert p.resolve_voice("IT", "it-IT-DiegoNeural").name == "it-IT-DiegoNeural"
        with pytest.raises(ConfigError):
            p.resolve_voice("IT", "it-IT-NopeNeural")                    # list known: unknown name
        with pytest.raises(ConfigError):
            p.resolve_voice("XX")
    finally:
        patcher.stop()


def test_voice_resolution_without_the_voice_list(clean_env):
    p, _, patcher = _azure(lambda r, n: AUDIO, voices=None)
    try:
        assert p.resolve_voice("IT").name == "it-IT-ElsaNeural"          # built-in table
        assert p.resolve_voice("IT", "it-IT-DiegoNeural") == Voice("it-IT-DiegoNeural", "it-IT")
        with pytest.raises(ConfigError):
            p.resolve_voice("NL")                                        # no table entry, no list
    finally:
        patcher.stop()


def _http(code, headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(b"detail"))


def test_rate_limit_is_waited_out_and_retried(clean_env):
    def script(req, n):
        if n == 1:
            raise _http(429, {"Retry-After": "2"})
        return AUDIO
    p, calls, patcher = _azure(script)
    try:
        with patch("ankitool.providers.base.time.sleep") as sl:
            assert p.synthesize("ciao", p.resolve_voice("IT")) == AUDIO
        assert len(calls) == 2 and sl.call_args_list[0][0][0] == 2     # wait() sleeps the Retry-After
    finally:
        patcher.stop()


def test_server_errors_give_up_after_retries(clean_env):
    def script(req, n):
        raise _http(503)
    p, calls, patcher = _azure(script)
    try:
        with patch("ankitool.providers.base.time.sleep"):
            assert p.synthesize("ciao", p.resolve_voice("IT")) is None
        assert len(calls) == p.retries
    finally:
        patcher.stop()


def test_rejected_key_is_a_config_error(clean_env):
    def script(req, n):
        raise _http(401)
    p, _, patcher = _azure(script)
    try:
        with pytest.raises(ConfigError, match="Translator-only"):
            p.synthesize("ciao", p.resolve_voice("IT"))
    finally:
        patcher.stop()


def test_tiny_reply_is_not_accepted_as_audio(clean_env):
    p, calls, patcher = _azure(lambda r, n: b"{}")
    try:
        with patch("ankitool.providers.base.time.sleep"):
            assert p.synthesize("ciao", p.resolve_voice("IT")) is None
    finally:
        patcher.stop()


def test_settings_validation(clean_env, monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "evil.com/x")
    with pytest.raises(ConfigError, match="valid region"):
        AzureSpeechProvider()
    monkeypatch.delenv("AZURE_SPEECH_REGION")
    with pytest.raises(ConfigError, match="region"):
        AzureSpeechProvider()
    monkeypatch.setenv("AZURE_SPEECH_REGION", "brazilsouth")
    monkeypatch.setenv("AZURE_SPEECH_TIER", "S9")
    with pytest.raises(ConfigError, match="TIER"):
        AzureSpeechProvider()
    monkeypatch.setenv("AZURE_SPEECH_TIER", "s0")
    monkeypatch.delenv("AZURE_SPEECH_KEY")
    with pytest.raises(ConfigError, match="API key"):
        AzureSpeechProvider()
    monkeypatch.setenv("AZURE_KEY", "multi")                             # multi-service key is accepted
    p = AzureSpeechProvider()
    assert p.key == "multi" and p.rpm == 600 and p.max_workers == 4


# ---- the operation, with fakes (no Anki, no network) ----
class FakeNote:
    def __init__(self, mid, **fields):
        self.mid, self.f = mid, dict(fields)

    def items(self):
        return list(self.f.items())

    def __setitem__(self, k, v):
        self.f[k] = v


class FakeAnki:
    def __init__(self, notes, cloze=()):
        self.notes, self.cloze_mids, self.media, self.saved = notes, set(cloze), {}, []

    def add_media(self, name, data):
        self.media[name] = data
        return name

    def update_notes(self, notes):
        self.saved = list(notes)


class FakeSpeech:
    name, label, extension, max_workers, rpm = "fake", "Fake speech", "mp3", 1, None
    on_wait = cancel_event = None

    def __init__(self, fail=False):
        self.asked, self.fail = [], fail

    def resolve_voice(self, lang, override=None):
        return Voice(override or f"{lang.lower()}-voice", lang.lower())

    def synthesize(self, text, voice, delivery=None):
        self.asked.append((text, voice.name))
        return None if self.fail else AUDIO + text.encode()

    def variant(self, delivery):
        return ""


def _op(tmp_path, notes, cloze=(), speech=None, **cfg):
    op = AudioOperation(speech or FakeSpeech(), AudioCache(tmp_path / "audio"), AudioConfig(**cfg))
    op.anki = FakeAnki(notes, cloze)
    return op


CHOSEN = {(1, "Word"): "IT"}


def test_only_notes_without_audio_get_audio_in_the_same_field(tmp_path):
    n_new = FakeNote(1, Word="il gatto", Meaning="o gato")
    n_audio = FakeNote(1, Word="la casa", Meaning="a casa [sound:x.mp3]")
    n_cloze = FakeNote(2, Word="{{c1::cane}}")
    op = _op(tmp_path, [n_new, n_audio, n_cloze], cloze={2})
    est = op.prepare({**CHOSEN, (2, "Word"): "IT"})
    assert (est.notes, est.with_audio, est.cloze, est.items, est.texts) == (3, 1, 1, 1, 1)
    op.synthesize()
    assert op.apply() == 1
    name = AudioCache.filename("fake", "it-voice", "il gatto")
    assert n_new.f == {"Word": f"il gatto [sound:{name}]", "Meaning": "o gato"}   # other field untouched
    assert n_audio.f["Word"] == "la casa" and n_cloze.f["Word"] == "{{c1::cane}}"
    assert op.anki.media[name].startswith(b"ID3") and op.anki.saved == [n_new]

class RateSpeech(FakeSpeech):
    seen = None

    def variant(self, delivery):
        return f"rate={delivery.rate}" if delivery.rate else ""

    def synthesize(self, text, voice, delivery=None):
        self.seen = delivery
        return super().synthesize(text, voice, delivery)


def test_delivery_changes_the_media_name_and_reaches_the_service(tmp_path):
    speech = RateSpeech()
    op = _op(tmp_path, [FakeNote(1, Word="il gatto")], speech=speech, rate="-15%")
    op.prepare(CHOSEN)
    assert op.items[0].name == AudioCache.filename("fake", "it-voice", "il gatto", "mp3", "rate=-15%")
    assert op.items[0].name != AudioCache.filename("fake", "it-voice", "il gatto")
    op.synthesize()
    assert speech.seen.rate == "-15%"

class StyleSpeech(FakeSpeech):
    def variant(self, delivery):
        return f"style={delivery.style}" if delivery.style else ""

    def synthesize(self, text, voice, delivery=None):
        self.seen = delivery
        return super().synthesize(text, voice, delivery)


def test_style_chosen_for_a_language_changes_name_and_reaches_the_service(tmp_path):
    speech = StyleSpeech()
    op = _op(tmp_path, [FakeNote(1, Word="il gatto")], speech=speech, style="chat", styles={"IT": "cheerful"})
    op.prepare(CHOSEN)
    assert op.items[0].name == AudioCache.filename("fake", "it-voice", "il gatto", "mp3", "style=cheerful")
    op.synthesize()
    assert speech.seen.style == "cheerful"


def test_picker_style_can_mean_no_style_and_other_languages_keep_the_default(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="il gatto")], speech=StyleSpeech(), style="chat", styles={"IT": ""})
    op.prepare(CHOSEN)
    assert op.items[0].name == AudioCache.filename("fake", "it-voice", "il gatto", "mp3")
    assert op._delivery("PT").style == "chat"


def test_preview_with_a_style_is_cached_separately(tmp_path):
    sp = StyleSpeech()
    op = _op(tmp_path, [], speech=sp)
    v = Voice("it-voice", "it")
    op.preview("ciao", v)
    op.preview("ciao", v, "cheerful")
    op.preview("ciao", v, "cheerful")
    assert len(sp.asked) == 2


def test_replace_redoes_notes_that_already_have_audio(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="il gatto [sound:old.mp3]")], replace=True)
    est = op.prepare(CHOSEN)
    assert (est.items, est.replaced, est.with_audio) == (1, 1, 0)
    assert op.items[0].value == "il gatto"            # the old tag is gone before the new one is added


def test_notes_with_audio_are_skipped_unless_replace(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="il gatto [sound:old.mp3]")])
    est = op.prepare(CHOSEN)
    assert (est.items, est.replaced, est.with_audio) == (0, 0, 1)


def test_cloze_can_be_included(tmp_path):
    n = FakeNote(2, Word="{{c1::cane::animal}} corre")
    op = _op(tmp_path, [n], cloze={2}, include_cloze=True)
    est = op.prepare({(2, "Word"): "IT"})
    assert est.items == 1 and op.items[0].text == "cane corre"


def test_same_text_is_requested_once_and_cached_texts_never(tmp_path):
    notes = [FakeNote(1, Word="ciao"), FakeNote(1, Word="<b>ciao</b>"), FakeNote(1, Word="grazie")]
    speech = FakeSpeech()
    op = _op(tmp_path, notes, speech=speech)
    est = op.prepare(CHOSEN)
    assert (est.items, est.texts, est.cached) == (3, 2, 0)
    assert op.synthesize() == 2 and sorted(t for t, _ in speech.asked) == ["ciao", "grazie"]
    op2 = _op(tmp_path, [FakeNote(1, Word="ciao")], speech=speech)                # a later run
    assert op2.prepare(CHOSEN).cached == 1
    assert op2.synthesize() == 0 and len(speech.asked) == 2


def test_long_and_letterless_texts_are_skipped(tmp_path):
    notes = [FakeNote(1, Word="x" * 11), FakeNote(1, Word="123 ..."), FakeNote(1, Word="<br>")]
    op = _op(tmp_path, notes, max_chars=10)
    est = op.prepare(CHOSEN)
    assert (est.items, est.too_long) == (0, 1)


def test_voice_override_needs_one_language(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="ciao")], voice="it-IT-DiegoNeural")
    with pytest.raises(ConfigError, match="single language"):
        op.prepare({(1, "Word"): "IT", (1, "Meaning"): "PT"})
    assert op.prepare(CHOSEN).items == 1 and op.items[0].voice.name == "it-IT-DiegoNeural"


def test_unavailable_service_keeps_nothing_half_written(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="ciao")], speech=FakeSpeech(fail=True))
    op.prepare(CHOSEN)
    with pytest.raises(ProviderUnavailable):
        op.synthesize()
    assert not (tmp_path / "audio").exists()


def test_language_choice_helpers(tmp_path):
    op = _op(tmp_path, [])
    detected = {(1, "Word"): ("it", 0.9, ""), (1, "Meaning"): ("PT", 0.95, ""), (1, "Note"): ("EN", 0.3, "")}
    assert op.language_options(detected) == [("IT", 1), ("PT", 1)]       # uncertain EN is left out
    assert op.plan(detected, {"IT"}) == {(1, "Word"): "IT"}
    assert op.validate_languages("it", dict(op.language_options(detected))) == {"IT"}
    with pytest.raises(ConfigError):
        op.validate_languages("DE", {"IT": 1})


def test_audio_command_arguments():
    o = parse_args(["audio", "deck.apkg", "--lang", "IT", "--field", "Basic:Word", "--dry-run"])
    assert (o.command, o.speech, o.lang, o.only_fields, o.dry_run) == ("audio", "azure", "IT", [("Basic", "Word")], True)
    assert o.max_chars == 400 and not o.include_cloze

def test_audio_rate_and_style_flags():
    o = parse_args(["audio", "deck.apkg", "--rate=-15%", "--style", "friendly"])
    assert (o.rate, o.style) == ("-15%", "friendly")
    o = parse_args(["audio", "deck.apkg"])
    assert o.rate is None and o.style is None

def test_chosen_voice_is_used_for_its_language(tmp_path):
    op = _op(tmp_path, [FakeNote(1, Word="il gatto")], voices={"IT": "it-other"})
    op.prepare(CHOSEN)
    assert op.items[0].voice.name == "it-other"


def test_preview_is_generated_once_and_reused(tmp_path):
    sp = FakeSpeech()
    op = _op(tmp_path, [], speech=sp)
    v = Voice("it-voice", "it")
    assert op.preview("ciao", v) == op.preview("ciao", v)
    assert len(sp.asked) == 1


def test_sample_text_is_a_typical_real_value(tmp_path):
    op = _op(tmp_path, [])
    op.anki.field_values = {(1, "Word"): ["a b", "il gatto", "la casa grande"]}
    assert op.sample_text(CHOSEN, "IT") == "il gatto"
