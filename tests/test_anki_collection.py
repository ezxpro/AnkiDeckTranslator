import pytest
from anki.collection import Collection, ExportAnkiPackageOptions

from ankitool.anki.collection import AnkiCollection


class FakeCache:
    """Stands in for TranslationCache: maps (output, source, text) -> translation."""
    def __init__(self, table):
        self.table = table

    def get(self, T, L, v):
        return self.table.get((T, L, v))


@pytest.fixture
def deck(tmp_path):
    """A small .apkg with two notes, built with the anki library itself."""
    path = tmp_path / "src.apkg"
    col = Collection(str(tmp_path / "build.anki2"))
    deck_id = col.decks.id("Test")
    model = col.models.by_name("Basic")
    for front, back in [("Hello there", "General Kenobi"), ("Good morning", "Bom dia")]:
        note = col.new_note(model)
        note["Front"], note["Back"] = front, back
        col.add_note(note, deck_id)
    col.export_anki_package(out_path=str(path), options=ExportAnkiPackageOptions(
        with_scheduling=False, with_deck_configs=False, with_media=True, legacy=True), limit=None)
    col.close()
    return path


def load(path):
    ac = AnkiCollection(path)
    ac.import_package()
    ac.load_notes()
    return ac


def test_apply_translations_and_export_round_trip(deck, tmp_path):
    ac = load(deck)
    mid = ac.notes[0].mid
    cache = FakeCache({("PT-BR", "EN", "Hello there"): "Olá"})
    chosen = {(mid, "Front"): ("EN", "PT-BR")}
    ac.apply_translations(chosen, cache)
    out = tmp_path / "out.apkg"
    ac.export(out)
    ac.close()

    check = load(out)
    fronts = sorted(n["Front"] for n in check.notes)
    backs = sorted(n["Back"] for n in check.notes)
    assert "Olá" in fronts and "Good morning" in fronts      # translated field changed
    assert backs == ["Bom dia", "General Kenobi"]            # untouched field kept as-is
    check.close()


def test_apply_without_cache_hit_changes_nothing(deck):
    ac = load(deck)
    before = sorted(n["Front"] for n in ac.notes)
    ac.apply_translations({(ac.notes[0].mid, "Front"): ("EN", "PT-BR")}, FakeCache({}))
    assert sorted(n["Front"] for n in ac.notes) == before
    ac.close()


def test_filter_fields_skip_removes_field(deck):
    ac = load(deck)
    ac.filter_fields(only=[], skip=[(None, "Back")])
    assert all(f == "Front" for (_, f) in ac.field_values)
    ac.close()


def test_close_removes_temp_folder(deck):
    ac = load(deck)
    tmp = ac.tmp_dir
    ac.close()
    assert not tmp.exists()