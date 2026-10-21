from pathlib import Path
import collections
import tempfile
from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ImportAnkiPackageOptions, ExportAnkiPackageOptions)

from ankitool.core.progress import Heartbeat, Reporter, track
from ankitool.core.text import TextTools


class AnkiCollection:
    """Importa o .apkg, expõe notas/campos, aplica traduções e exporta."""

    def __init__(self, src, keep_scheduling=False, legacy=True, rep=None):
        self.rep = rep or Reporter()
        self.src = Path(src)
        self.keep_scheduling, self.legacy = keep_scheduling, legacy
        self.col = None
        self.notes = []
        self.model_names = {}                                  # mid -> nome do modelo
        self.field_values = collections.defaultdict(list)      # (mid, campo) -> valores com texto

    def import_package(self):
        self.rep.log(f"importando {self.src} ({self.src.stat().st_size / 1e6:.1f} MB)")
        db_path = Path(tempfile.mkdtemp()) / "collection.anki2"
        self.col = Collection(str(db_path))
        with Heartbeat(self.rep, "importando baralho"):
            self.col.import_anki_package(ImportAnkiPackageRequest(
                package_path=str(self.src), options=ImportAnkiPackageOptions(
                    with_scheduling=self.keep_scheduling,
                    with_deck_configs=self.keep_scheduling)))

    def load_notes(self):
        for nid in track(self.rep, self.col.find_notes(""), "carregando notas"):
            n = self.col.get_note(nid)
            self.notes.append(n)
            if n.mid not in self.model_names:
                self.model_names[n.mid] = n.note_type()["name"]
            for f, v in n.items():
                if len(TextTools.plain(v)) >= 3:
                    self.field_values[(n.mid, f)].append(v)
        self.rep.log("notas:", len(self.notes))

    def filter_fields(self, only, skip):
        """Tira de field_values os campos que não devem ser traduzidos (--field / --skip-field).
        Cada item é (tipo_de_nota ou None, campo). Devolve os itens de 'only' sem correspondência."""
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
        for n in track(self.rep, self.notes, "aplicando traduções"):
            changed = False
            for f, v in n.items():
                c = chosen.get((n.mid, f))
                if c:
                    new = cache.get(c[1], c[0], v)
                    if new is not None and new != v:
                        n[f] = new
                        changed = True
            if changed:
                self.col.update_note(n)

    def deck_list(self):
        return [(d.id, d.name) for d in self.col.decks.all_names_and_ids(
            skip_empty_default=True, include_filtered=False) if d.name != "Default"]

    def notetypes(self):
        return self.col.models.all()

    def note_count(self, mid):
        return len(self.col.find_notes(f"mid:{mid}"))

    def export(self, out_path):
        with Heartbeat(self.rep, "exportando"):
            self.col.export_anki_package(out_path=str(out_path), options=ExportAnkiPackageOptions(
                with_scheduling=self.keep_scheduling, with_deck_configs=self.keep_scheduling,
                with_media=True, legacy=self.legacy),
                limit=None)

    def close(self):
        if self.col:
            self.col.close()
            self.col = None
