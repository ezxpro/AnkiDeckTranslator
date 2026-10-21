from dataclasses import dataclass
import re

from ankitool.core.errors import ConfigError
from ankitool.core.progress import Reporter


@dataclass
class ReverseConfig:
    notetypes: list = None           # note type names, or None = all / ask
    swap: list = None                # ["Field1=Field2", ...]

class CardReverser:
    """Swaps front and back by exchanging, in the card templates, the references to PAIRS of
    fields ({{Word}} <-> {{Translation}}). Several pairs are swapped at once, which is needed when
    each side has audio, sentence fields etc. The note content does not change and the operation
    is reversible: running it again undoes it. Only standard note types with 2+ fields are included
    (the cloze type, which has a single field, is left out)."""

    REF = re.compile(r"\{\{([#^/]?)((?:[^{}:]*:)*)([^{}:]+)\}\}")
    STANDARD = 0                                         # in Anki, cloze is 1

    def __init__(self, anki, rep=None):
        self.anki, self.rep = anki, rep or Reporter()
        self.counts = {}                                 # note type id -> number of notes

    # ---- templates ----
    @classmethod
    def swap_refs(cls, tmpl, mapping):
        """Swaps the field references in a template, in a single pass, according to
        'mapping' ({A: B, B: A}). Preserves filters and sections: {{type:A}}, {{#A}}...{{/A}}."""
        def sub(m):
            prefix, filters, name = m.groups()
            new = mapping.get(name.strip())
            if new is None:
                return m.group(0)
            return "{{" + prefix + filters + new + "}}"
        return cls.REF.sub(sub, tmpl)

    @classmethod
    def refs(cls, tmpl, names):
        """Fields referenced in the template, in the order they appear."""
        seen = []
        for m in cls.REF.finditer(tmpl):
            n = m.group(3).strip()
            if n in names and n not in seen:
                seen.append(n)
        return seen

    @classmethod
    def layout(cls, nt):
        """(fields shown on the front, fields that only appear on the back), from the 1st template."""
        names = [f["name"] for f in nt["flds"]]
        t = nt["tmpls"][0]
        front = cls.refs(t["qfmt"], names)
        back = [n for n in cls.refs(t["afmt"], names) if n not in front]
        return front, back

    @classmethod
    def suggest_pairs(cls, nt):
        """Pairs of fields to swap. 1st: by name, when one field's name ends with another's
        ('Word' / 'TranslationWord', 'WordAudio' / 'TranslationWordAudio').
        2nd: by template layout (1st front field / 1st back-only field).
        3rd: the first two fields."""
        names = [f["name"] for f in nt["flds"]]
        pairs, used = [], set()
        for a in names:
            if a in used:
                continue
            cands = [b for b in names if b not in used and len(b) > len(a)
                     and b.lower().endswith(a.lower())]
            if len(cands) == 1:
                pairs.append((a, cands[0]))
                used.update((a, cands[0]))
        if pairs:
            return pairs
        front, back = cls.layout(nt)
        if front and back:
            return [(front[0], back[0])]
        return [(names[0], names[1])]

    @staticmethod
    def parse_pairs(raw, names):
        """'A=B, C=D' -> [(A, B), (C, D)] using the real field spelling; None if invalid
        (unknown field, repeated, or paired with itself)."""
        lower = {n.lower(): n for n in names}
        pairs, used = [], set()
        for item in re.split(r"[,;]+", raw):
            if not item.strip():
                continue
            parts = [p.strip().lower() for p in re.split(r"<->|=", item)]
            if (len(parts) != 2 or parts[0] == parts[1] or used & set(parts)
                    or any(p not in lower for p in parts)):
                return None
            used.update(parts)
            pairs.append((lower[parts[0]], lower[parts[1]]))
        return pairs or None

    # ---- selection ----
    def eligible(self):
        out, skipped = [], 0
        for nt in self.anki.notetypes():
            n = self.anki.note_count(nt["id"])
            if n == 0:
                continue                                 # Anki's default types with no notes
            self.counts[nt["id"]] = n
            if nt["type"] == self.STANDARD and len(nt["flds"]) >= 2:
                out.append(nt)
            else:
                skipped += 1
        if skipped:
            self.rep.log(f"note types skipped (cloze or fewer than 2 fields): {skipped}")
        return sorted(out, key=lambda nt: nt["name"].lower())

    def select_by_name(self, cands, names):
        wanted = {n.lower(): n for n in names}
        chosen = [nt for nt in cands if nt["name"].lower() in wanted]
        missing = set(wanted) - {nt["name"].lower() for nt in chosen}
        if missing:
            raise ConfigError(
                f"Note type(s) not found: {', '.join(wanted[m] for m in sorted(missing))}. "
                f"Available: {', '.join(nt['name'] for nt in cands)}")
        return chosen

    def pairs_for(self, nt, swap=None):
        """Pairs to swap: those in 'swap' (["A=B"]) or the suggested ones. None if 'swap' doesn't fit."""
        if swap:
            names = [f["name"] for f in nt["flds"]]
            pairs = self.parse_pairs(",".join(swap), names)
            if not pairs:
                self.rep.warn(f" {nt['name']}: does not have the fields from --swap; skipped")
            return pairs
        return self.suggest_pairs(nt)

    def build_plan(self, cfg):
        """Plan without questions (library use / --yes): [(note type, pairs)]."""
        cands = self.eligible()
        chosen = self.select_by_name(cands, cfg.notetypes) if cfg.notetypes else cands
        plan = []
        for nt in chosen:
            pairs = self.pairs_for(nt, cfg.swap)
            if pairs:
                plan.append((nt, pairs))
        return plan

    # ---- applying ----
    def apply(self, plan):
        models = self.anki.col.models
        save = getattr(models, "update_dict", None) or models.save
        done = 0
        for nt, pairs in plan:
            mapping = {}
            for a, b in pairs:
                mapping[a], mapping[b] = b, a
            changed = False
            for t in nt["tmpls"]:
                q, ans = self.swap_refs(t["qfmt"], mapping), self.swap_refs(t["afmt"], mapping)
                changed |= (q != t["qfmt"] or ans != t["afmt"])
                t["qfmt"], t["afmt"] = q, ans
            if not changed:
                self.rep.log(f" {nt['name']}: no template uses the chosen fields; skipped")
                continue
            save(nt)
            done += 1
        self.rep.log(f"note types reversed: {done}/{len(plan)}")
        return done
