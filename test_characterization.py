import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anki_translator as at          # ajuste para o nome real do arquivo


def test_parse_numbers():
    assert at.Ui.parse_numbers("1,3,5-8", 10) == {1, 3, 5, 6, 7, 8}
    assert at.Ui.parse_numbers("11", 10) is None
    assert at.Ui.parse_numbers("abc", 10) is None


def test_lang_base():
    assert at.lang_base("pt-BR") == "PT"
    assert at.lang_base("PT_br") == "PT"
    assert at.lang_base(None) == ""


def test_mask_roundtrip_and_prot():
    s = "olá [sound:a.mp3] {{c1::x}}"
    assert at.TextTools.unmask(at.TextTools.mask(s)) == s
    assert at.TextTools.PROT.findall(s) == ["[sound:a.mp3]", "{{c1::", "}}"]


def test_plain():
    assert at.TextTools.plain("<b>oi</b> [sound:a.mp3]") == "oi"


def test_final_name():
    newseg = {("A",): "X", ("A", "B"): "Y"}
    assert at.DeckRenamer.final_name("A::B", newseg) == "X::Y"
    assert at.DeckRenamer.final_name("C", newseg) == "C"


def test_swap_refs_keeps_filters_and_sections():
    t = "{{Front}}<hr>{{type:Back}}{{#Audio}}x{{/Audio}}{{FrontSide}}"
    m = {"Front": "Back", "Back": "Front"}
    assert at.CardReverser.swap_refs(t, m) == \
        "{{Back}}<hr>{{type:Front}}{{#Audio}}x{{/Audio}}{{FrontSide}}"


def test_parse_pairs():
    assert at.CardReverser.parse_pairs("word=translation", ["Word", "Translation"]) \
        == [("Word", "Translation")]
    assert at.CardReverser.parse_pairs("word=word", ["Word", "Translation"]) is None
    assert at.CardReverser.parse_pairs("x=y", ["Word", "Translation"]) is None


def test_suggest_pairs_by_name():
    names = ["Word", "TranslationWord", "WordAudio", "TranslationWordAudio"]
    nt = {"flds": [{"name": n} for n in names], "tmpls": [{"qfmt": "", "afmt": ""}]}
    assert at.CardReverser.suggest_pairs(nt) == [
        ("Word", "TranslationWord"), ("WordAudio", "TranslationWordAudio")]


def test_cache_v2_migration(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 2, "trans": {"PT-BR": {"ciao": "olá"}}}))
    c = at.TranslationCache(p, {"PT-BR"})
    assert c.get("PT-BR", "IT", "ciao") == "olá"