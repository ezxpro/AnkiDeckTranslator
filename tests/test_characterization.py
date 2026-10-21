import json

from ankitool.cache.translation import TranslationCache
from ankitool.cli.console import Ui
from ankitool.core.text import TextTools, lang_base
from ankitool.ops.rename import DeckRenamer
from ankitool.ops.reverse import CardReverser


def test_parse_numbers():
    assert Ui.parse_numbers("1,3,5-8", 10) == {1, 3, 5, 6, 7, 8}
    assert Ui.parse_numbers("11", 10) is None
    assert Ui.parse_numbers("abc", 10) is None


def test_lang_base():
    assert lang_base("pt-BR") == "PT"
    assert lang_base("PT_br") == "PT"
    assert lang_base(None) == ""


def test_mask_roundtrip_and_prot():
    s = "olá [sound:a.mp3] {{c1::x}}"
    assert TextTools.unmask(TextTools.mask(s)) == s
    assert TextTools.PROT.findall(s) == ["[sound:a.mp3]", "{{c1::", "}}"]


def test_plain():
    assert TextTools.plain("<b>oi</b> [sound:a.mp3]") == "oi"


def test_final_name():
    newseg = {("A",): "X", ("A", "B"): "Y"}
    assert DeckRenamer.final_name("A::B", newseg) == "X::Y"
    assert DeckRenamer.final_name("C", newseg) == "C"


def test_swap_refs_keeps_filters_and_sections():
    t = "{{Front}}<hr>{{type:Back}}{{#Audio}}x{{/Audio}}{{FrontSide}}"
    m = {"Front": "Back", "Back": "Front"}
    assert CardReverser.swap_refs(t, m) == \
        "{{Back}}<hr>{{type:Front}}{{#Audio}}x{{/Audio}}{{FrontSide}}"


def test_parse_pairs():
    assert CardReverser.parse_pairs("word=translation", ["Word", "Translation"]) \
        == [("Word", "Translation")]
    assert CardReverser.parse_pairs("word=word", ["Word", "Translation"]) is None
    assert CardReverser.parse_pairs("x=y", ["Word", "Translation"]) is None


def test_suggest_pairs_by_name():
    names = ["Word", "TranslationWord", "WordAudio", "TranslationWordAudio"]
    nt = {"flds": [{"name": n} for n in names], "tmpls": [{"qfmt": "", "afmt": ""}]}
    assert CardReverser.suggest_pairs(nt) == [
        ("Word", "TranslationWord"), ("WordAudio", "TranslationWordAudio")]


def test_cache_v2_migration(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 2, "trans": {"PT-BR": {"ciao": "olá"}}}))
    c = TranslationCache(p, {"PT-BR"})
    assert c.get("PT-BR", "IT", "ciao") == "olá"