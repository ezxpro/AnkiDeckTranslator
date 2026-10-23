import pytest

from ankitool.cache.translation import TranslationCache


@pytest.fixture
def cache(tmp_path):
    c = TranslationCache(tmp_path / "cache.sqlite")
    yield c
    c.close()


def test_scopes(cache):
    cache.put("PT-BR", "EN", "bank", "banco-deepl", "deepl")
    cache.put("PT-BR", "EN", "bank", "banco-a", "gemini", "model-a")
    cache.put("PT-BR", "EN", "bank", "banco-b", "gemini", "model-b")
    assert cache.get("PT-BR", "EN", "bank", "gemini", "model-a", "isolated") == "banco-a"
    assert cache.get("PT-BR", "EN", "bank", "gemini", "model-c", "isolated") is None
    assert cache.get("PT-BR", "EN", "bank", "gemini", "model-c", "provider") in {"banco-a", "banco-b"}
    assert cache.get("PT-BR", "EN", "bank", "groq", "x", "provider") is None
    assert cache.get("PT-BR", "EN", "bank", "groq", "x", "shared") == "banco-deepl"


def test_manual_wins_in_every_scope(cache):
    cache.put("PT-BR", "EN", "bank", "banco-deepl", "deepl")
    cache.put_manual("PT-BR", "EN", "bank", "banco (corrigido)")
    for mode in ("shared", "provider", "isolated"):
        assert cache.get("PT-BR", "EN", "bank", "deepl", "", mode) == "banco (corrigido)"


def test_services_cannot_write_manual(cache):
    with pytest.raises(ValueError):
        cache.put("PT-BR", "EN", "bank", "x", "manual")


def test_invalidate_keeps_manual(cache):
    cache.put("PT-BR", "EN", "bank", "banco-deepl", "deepl")
    cache.put_manual("PT-BR", "EN", "bank", "banco (corrigido)")
    assert cache.invalidate() == 1
    assert cache.count() == 1


def test_one_text_row_for_many_services(cache):
    for svc in ("deepl", "gemini", "groq"):
        cache.put("PT-BR", "EN", "bank", "banco", svc)
    assert cache.conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0] == 1


def test_detector_does_not_overwrite_manual_language(cache):
    cache.set_lang("bank", "EN", "deepl")
    cache.set_lang("bank", "PT", "manual")
    cache.set_lang("bank", "ES", "deepl")
    assert cache.lang["bank"] == "PT"