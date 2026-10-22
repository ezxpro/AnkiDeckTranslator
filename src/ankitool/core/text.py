import hashlib
import re


class TextTools:
    PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
    WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)

    @staticmethod
    def h(s):
        """Short, stable hash of a text: the cache stores this instead of the original text."""
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

    @staticmethod
    def has_letters(s):
        """False for texts with no letters (numbers, punctuation, symbols): nothing to translate."""
        return re.search(r"[^\W\d_]", TextTools.plain(s)) is not None

h = TextTools.h

def lang_base(code):
    """'pt-BR' / 'PT_br' / 'PT' -> 'PT': compares languages ignoring region and case."""
    return next((p for p in re.split(r"[-_]", (code or "").upper()) if p), "")
