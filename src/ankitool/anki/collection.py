from pathlib import Path
import collections
import tempfile
import shutil
import zipfile
from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ImportAnkiPackageOptions, ExportAnkiPackageOptions)


from ankitool.core.progress import Heartbeat, Reporter, format_import_progress, track
from ankitool.core.text import TextTools


class AnkiCollection:
    """Imports the .apkg, exposes notes/fields, applies translations and exports."""

    CLOZE = 1                                                  # Anki's note type kind for cloze

    def __init__(self, src, keep_scheduling=False, legacy=True, rep=None):
        self.rep = rep or Reporter()
        self.src = Path(src)
        self.keep_scheduling, self.legacy = keep_scheduling, legacy
        self.col = None
        self.tmp_dir = None
        self.notes = []
        self.model_names = {}                                  # mid -> note type name
        self.cloze_mids = set()                                # note types of the cloze kind
        self.field_values = collections.defaultdict(list)      # (mid, field) -> values with text

    def _anki_progress(self):
        """Anki's own progress text during import/export, or '' if this Anki version doesn't expose it."""
        try:
            p = self.col._backend.latest_progress()
            return next((getattr(p, k) for k in ("importing", "exporting") if p.HasField(k)), "")
        except Exception:
            return ""

    def _media_total(self):
        """Media files inside the .apkg (zip entries named with a number); 0 if it cannot be read."""
        try:
            with zipfile.ZipFile(self.src) as z:
                return sum(1 for n in z.namelist() if n.isdigit())
        except (OSError, zipfile.BadZipFile):
            return 0

    def import_package(self):
        total = self._media_total()
        self.rep.log(f"importing {self.src} ({self.src.stat().st_size / 1e6:.1f} MB"
                     + (f", {total} media files)" if total else ")"))
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.col = Collection(str(self.tmp_dir / "collection.anki2"))
        with Heartbeat(self.rep, "importing deck",
                       status=lambda: format_import_progress(self._anki_progress(), total)):
            self.col.import_anki_package(ImportAnkiPackageRequest(
                package_path=str(self.src), options=ImportAnkiPackageOptions(
                    with_scheduling=self.keep_scheduling,
                    with_deck_configs=self.keep_scheduling)))
            
    def load_notes(self):
        nids = self.col.find_notes("")
        self.rep.log(f"reading {len(nids)} notes...")
        get_notes = getattr(self.col, "get_notes", None)      # batch read in newer anki versions
        notes = get_notes(nids) if get_notes else [self.col.get_note(i) for i in nids]
        for n in track(self.rep, notes, "loading notes"):
            self.notes.append(n)
            if n.mid not in self.model_names:
                nt = n.note_type()
                self.model_names[n.mid] = nt["name"]
                if nt["type"] == self.CLOZE:
                    self.cloze_mids.add(n.mid)
            for f, v in n.items():
                if len(TextTools.plain(v)) >= 3:
                    self.field_values[(n.mid, f)].append(v)
        self.rep.log("notes:", len(self.notes))

    def filter_fields(self, only, skip):
        """Removes from field_values the fields that must not be translated (--field / --skip-field).
        Each item is (note type or None, field). Returns the items in 'only' with no match."""
        def match(specs, key):
            mid, f = key
            return any(fld.lower() == f.lower()
                       and (nt is None or nt.lower() == self.model_names[mid].lower())
                       for nt, fld in specs)
        keys = list(self.field_values)
        unmatched = [sp for sp in only if not any(match([sp], k) for k in keys)]
        for k in keys:
            if (only and not match(only, k)) or (skip and match(skip, k)):
                del self.field_values[k]
        return unmatched

    def apply_translations(self, chosen, cache):
        changed = []
        for n in track(self.rep, self.notes, "applying translations"):
            dirty = False
            for f, v in n.items():
                c = chosen.get((n.mid, f))
                if c:
                    new = cache.get(c[1], c[0], v)
                    if new is not None and new != v:
                        n[f] = new
                        dirty = True
            if dirty:
                changed.append(n)
        for i in range(0, len(changed), 500):                 # one backend call per chunk, not per note
            self.col.update_notes(changed[i:i + 500])
        self.rep.log(f"notes updated: {len(changed)}")

    def add_media(self, name, data):
        """Stores a file in the collection's media; returns the name Anki kept for it
        (the same name unless a different file already uses it)."""
        return self.col.media.write_data(name, data)

    def update_notes(self, notes):
        """Saves notes changed in place (one backend call per chunk, not per note)."""
        for i in range(0, len(notes), 500):
            self.col.update_notes(notes[i:i + 500])

    def deck_list(self):
        return [(d.id, d.name) for d in self.col.decks.all_names_and_ids(
            skip_empty_default=True, include_filtered=False) if d.name != "Default"]

    def notetypes(self):
        return self.col.models.all()

    def note_count(self, mid):
        return len(self.col.find_notes(f"mid:{mid}"))

    def export(self, out_path):
        with Heartbeat(self.rep, "exporting", status=self._anki_progress):
            self.col.export_anki_package(out_path=str(out_path), options=ExportAnkiPackageOptions(
                with_scheduling=self.keep_scheduling, with_deck_configs=self.keep_scheduling,
                with_media=True, legacy=self.legacy),
                limit=None)

    def close(self):
        if self.col:
            self.col.close()
            self.col = None
        if self.tmp_dir:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)   # remove the temp copy of the deck
            self.tmp_dir = None
