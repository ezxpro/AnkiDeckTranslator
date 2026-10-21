from dataclasses import dataclass
import re

from ankitool.core.errors import ConfigError
from ankitool.core.progress import Reporter


@dataclass
class ReverseConfig:
    notetypes: list = None           # nomes de tipos de nota, ou None = todos/perguntar
    swap: list = None                # ["Campo1=Campo2", ...]

class CardReverser:
    """Inverte frente e verso trocando, nos templates dos cartões, as referências a PARES de
    campos ({{Palavra}} <-> {{Tradução}}). Vários pares são trocados de uma vez, o que é
    necessário quando cada lado tem campos de áudio, frase etc. O conteúdo das notas não
    muda e a operação é reversível: rodar de novo desfaz. Só entram tipos de nota padrão com
    2+ campos (o tipo cloze, que tem um campo único, fica de fora)."""

    REF = re.compile(r"\{\{([#^/]?)((?:[^{}:]*:)*)([^{}:]+)\}\}")
    STANDARD = 0                                         # no Anki, cloze é 1

    def __init__(self, anki, rep=None):
        self.anki, self.rep = anki, rep or Reporter()
        self.counts = {}                                 # id do tipo de nota -> nº de notas

    # ---- templates ----
    @classmethod
    def swap_refs(cls, tmpl, mapping):
        """Troca as referências a campos num template, numa única passada, conforme
        'mapping' ({A: B, B: A}). Preserva filtros e seções: {{type:A}}, {{#A}}...{{/A}}."""
        def sub(m):
            prefix, filters, name = m.groups()
            new = mapping.get(name.strip())
            if new is None:
                return m.group(0)
            return "{{" + prefix + filters + new + "}}"
        return cls.REF.sub(sub, tmpl)

    @classmethod
    def refs(cls, tmpl, names):
        """Campos citados no template, na ordem em que aparecem."""
        seen = []
        for m in cls.REF.finditer(tmpl):
            n = m.group(3).strip()
            if n in names and n not in seen:
                seen.append(n)
        return seen

    @classmethod
    def layout(cls, nt):
        """(campos mostrados na frente, campos que só aparecem no verso), pelo 1º template."""
        names = [f["name"] for f in nt["flds"]]
        t = nt["tmpls"][0]
        front = cls.refs(t["qfmt"], names)
        back = [n for n in cls.refs(t["afmt"], names) if n not in front]
        return front, back

    @classmethod
    def suggest_pairs(cls, nt):
        """Pares de campos a trocar. 1º: por nome, quando o nome de um campo termina com o
        de outro ('Word' / 'TranslationWord', 'WordAudio' / 'TranslationWordAudio').
        2º: pelo layout do template (1º campo da frente / 1º campo só do verso).
        3º: os dois primeiros campos."""
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
        """'A=B, C=D' -> [(A, B), (C, D)] com a grafia real dos campos; None se inválido
        (campo inexistente, repetido ou pareado consigo mesmo)."""
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

    # ---- seleção ----
    def eligible(self):
        out, skipped = [], 0
        for nt in self.anki.notetypes():
            n = self.anki.note_count(nt["id"])
            if n == 0:
                continue                                 # tipos padrão do Anki sem notas
            self.counts[nt["id"]] = n
            if nt["type"] == self.STANDARD and len(nt["flds"]) >= 2:
                out.append(nt)
            else:
                skipped += 1
        if skipped:
            self.rep.log(f"tipos de nota ignorados (cloze ou com menos de 2 campos): {skipped}")
        return sorted(out, key=lambda nt: nt["name"].lower())

    def select_by_name(self, cands, names):
        wanted = {n.lower(): n for n in names}
        chosen = [nt for nt in cands if nt["name"].lower() in wanted]
        missing = set(wanted) - {nt["name"].lower() for nt in chosen}
        if missing:
            raise ConfigError(
                f"Tipo(s) de nota não encontrado(s): {', '.join(wanted[m] for m in sorted(missing))}. "
                f"Disponíveis: {', '.join(nt['name'] for nt in cands)}")
        return chosen

    def pairs_for(self, nt, swap=None):
        """Pares a trocar: os de 'swap' (["A=B"]) ou os sugeridos. None se 'swap' não serve."""
        if swap:
            names = [f["name"] for f in nt["flds"]]
            pairs = self.parse_pairs(",".join(swap), names)
            if not pairs:
                self.rep.warn(f" {nt['name']}: não tem os campos de --swap; ignorado")
            return pairs
        return self.suggest_pairs(nt)

    def build_plan(self, cfg):
        """Plano sem perguntas (uso como biblioteca / --yes): [(tipo_de_nota, pares)]."""
        cands = self.eligible()
        chosen = self.select_by_name(cands, cfg.notetypes) if cfg.notetypes else cands
        plan = []
        for nt in chosen:
            pairs = self.pairs_for(nt, cfg.swap)
            if pairs:
                plan.append((nt, pairs))
        return plan

    # ---- aplicação ----
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
                self.rep.log(f" {nt['name']}: nenhum template usa os campos escolhidos; ignorado")
                continue
            save(nt)
            done += 1
        self.rep.log(f"tipos de nota invertidos: {done}/{len(plan)}")
        return done
