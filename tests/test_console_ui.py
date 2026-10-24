import contextlib
import io

import pytest

from ankitool.cli import console
from ankitool.core.errors import Cancelled
from ankitool.core.progress import format_import_progress
from ankitool.providers.base import Language

LANGS = [Language("FR", "French"), Language("IT", "Italian"), Language("PT-BR", "Portuguese (Brazilian)")]


def _pick(monkeypatch, keys, default=None):
    it = iter(keys)
    monkeypatch.setattr(console, "_interactive", lambda: True)
    monkeypatch.setattr(console, "read_key", lambda: next(it))
    with contextlib.redirect_stdout(io.StringIO()):
        return console.pick_language("Pick", LANGS, default)


def test_picker_filters_by_typing_any_case(monkeypatch):
    assert _pick(monkeypatch, ["F", "r", "enter"]) == "FR"
    assert _pick(monkeypatch, list("brazil") + ["enter"]) == "PT-BR"


def test_picker_moves_with_arrows_from_the_default(monkeypatch):
    assert _pick(monkeypatch, ["up", "enter"], default="PT-BR") == "IT"
    assert _pick(monkeypatch, ["down", "enter"], default="PT-BR") == "FR"      # wraps around


def test_picker_returns_typed_text_when_nothing_matches(monkeypatch):
    assert _pick(monkeypatch, list("klingon") + ["enter"]) == "klingon"


def test_picker_esc_clears_the_filter_then_cancels(monkeypatch):
    assert _pick(monkeypatch, ["x", "esc", "enter"]) == "FR"                  # filter cleared, first item
    with pytest.raises(Cancelled):
        _pick(monkeypatch, ["esc"])


def test_picker_is_skipped_without_a_terminal(monkeypatch):
    monkeypatch.setattr(console, "_interactive", lambda: False)
    assert console.pick_language("Pick", LANGS) is None


def test_import_progress_shows_share_and_drops_bidi_marks():
    assert format_import_progress("Imported \u20682669\u2069 media files", 5729) == "Imported 2669 media files of 5729 (46%)"
    assert format_import_progress("Imported \u20682669\u2069 media files") == "Imported 2669 media files"      # no media in the deck
    assert format_import_progress("Processed \u20681200\u2069 notes...", 5729) == "Processed 1200 notes..."     # other phases untouched
    assert format_import_progress(None, 10) == ""