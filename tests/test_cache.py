"""One cache for all services (cache.json v4): shared vs isolated reads, per-service writes, deck names."""
import json

from ankitool.cache.translation import CacheView, TranslationCache
from ankitool.core.progress import Reporter


def new(tmp_path):
    return TranslationCache(tmp_path / "cache.json", Reporter())


def test_services_keep_their_own_translation_of_the_same_sentence(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "IT", "ciao", "olá", "deepl")
    c.put("PT-BR", "IT", "ciao", "oi!", "gemini")       # does not overwrite DeepL's
    assert c.get("PT-BR", "IT", "ciao", "deepl", "isolated") == "olá"
    assert c.get("PT-BR", "IT", "ciao", "gemini", "isolated") == "oi!"
    assert c.count() == 2


def test_shared_reuses_others_and_isolated_does_not(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "IT", "ciao", "olá", "deepl")
    assert c.get("PT-BR", "IT", "ciao", "gemini", "shared") == "olá"
    assert c.get("PT-BR", "IT", "ciao", "gemini", "isolated") is None


def test_shared_prefers_own_then_alphabetical(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "IT", "ciao", "from-openai", "openai")
    c.put("PT-BR", "IT", "ciao", "from-deepl", "deepl")
    assert c.get("PT-BR", "IT", "ciao", "groq", "shared") == "from-deepl"
    c.put("PT-BR", "IT", "ciao", "from-groq", "groq")
    assert c.get("PT-BR", "IT", "ciao", "groq", "shared") == "from-groq"


def test_view_writes_only_its_own_entry(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "IT", "ciao", "olá", "deepl")
    CacheView(c, "gemini", "shared").write("PT-BR", "IT", "ciao", "oi")
    assert c.get("PT-BR", "IT", "ciao", "deepl", "isolated") == "olá"
    assert c.get("PT-BR", "IT", "ciao", "gemini", "isolated") == "oi"


def test_same_output_from_different_sources_stays_separate(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "EN", "Hello", "Olá", "deepl")
    assert c.get("PT-BR", "EN", "Hi", "deepl") is None


def test_deck_names_follow_the_same_modes(tmp_path):
    c = new(tmp_path)
    c.deck_put("PT-BR", "IT", "Scuola", "Escola", "deepl")
    assert c.deck_get("PT-BR", "IT", "Scuola", "gemini", "isolated") is None
    assert c.deck_get("PT-BR", "IT", "Scuola", "gemini", "shared") == "Escola"


def test_save_and_reload(tmp_path):
    c = new(tmp_path)
    c.put("PT-BR", "IT", "ciao", "olá", "deepl")
    c.lang["h"] = "IT"
    c.deck_put("PT-BR", "IT", "Scuola", "Escola", "deepl")
    c.save()
    data = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert data["version"] == 4 and set(data) == {"version", "trans", "lang", "decks"}
    again = new(tmp_path)
    assert again.get("PT-BR", "IT", "ciao", "deepl") == "olá"
    assert again.lang == {"h": "IT"}


def test_unknown_file_is_kept_as_backup_not_read(tmp_path):
    (tmp_path / "cache.json").write_text(json.dumps({"version": 3, "trans": {}}))
    c = new(tmp_path)
    assert c.count() == 0
    assert (tmp_path / "cache.json.bak").exists()
