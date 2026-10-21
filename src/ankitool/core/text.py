import hashlib
import re


class TextTools:
    PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
    WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)

    @staticmethod
    def h(s):
        """Hash curto e estável de um texto: o cache guarda isso no lugar do texto original."""
        return hashlib.blake2b(s.encode("utf-8"), digest_size=12).hexdigest()

    @classmethod
    def mask(cls, t):
        return cls.PROT.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', t)

    @classmethod
    def unmask(cls, t):
        return cls.WRAP.sub(r"\1", t)

    @staticmethod
    def plain(s):
        return re.sub(r"\[sound:[^\]]+\]|<[^>]+>|&\w+;|\{\{c\d+::|\}\}", "", s).strip()

h = TextTools.h

def lang_base(code):
    """'pt-BR' / 'PT_br' / 'PT' -> 'PT': compara idiomas ignorando região e maiúsculas."""
    return re.split(r"[-_]", (code or "").upper())[0]
