import html
import re


class TextTools:
    PROT = re.compile(r"\[sound:[^\]]+\]|\{\{c\d+::|\}\}")
    WRAP = re.compile(r'<span translate="no">(.*?)</span>', re.S)
    TAGS = re.compile(r"</?[A-Za-z][A-Za-z0-9]*")

    @classmethod
    def shape(cls, t):
        """What must survive translation: markers in order, the multiset of HTML tags, and
        balanced angle brackets (catches a dropped '>' as in '</li</ul>')."""
        return (cls.PROT.findall(t), sorted(cls.TAGS.findall(t)), t.count("<"), t.count(">"))

    @classmethod
    def mask(cls, t):
        return cls.PROT.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', t)

    @classmethod
    def unmask(cls, t):
        return cls.WRAP.sub(r"\1", t)

    @staticmethod
    def plain(s):
        return re.sub(r"\[sound:[^\]]+\]|<[^>]+>|&\w+;|\{\{c\d+::|\}\}", "", s).strip()

    _BLOCK = re.compile(r"<\s*(?:br|/?(?:div|p|li|ul|ol|tr|td|h[1-6]))\b[^>]*>", re.I)
    _CLOZE = re.compile(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}", re.S)
    _NOISE = re.compile(r"\[sound:[^\]]+\]|<style.*?</style>|<script.*?</script>|<[^>]+>", re.S | re.I)
    _XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

    @classmethod
    def speakable(cls, s):
        """What a voice should read: no tags, [sound:...] or cloze markers (the cloze answer is
        read, its hint is not), HTML entities decoded, whitespace collapsed."""
        s = cls._BLOCK.sub(" ", s)
        s = cls._CLOZE.sub(r"\1", s)
        s = html.unescape(cls._NOISE.sub("", s)).replace("\xa0", " ")
        return " ".join(cls._XML_BAD.sub("", s).split())

    @staticmethod
    def has_letters(s):
        """False for texts with no letters (numbers, punctuation, symbols): nothing to translate."""
        return re.search(r"[^\W\d_]", TextTools.plain(s)) is not None


def lang_base(code):
    """'pt-BR' / 'PT_br' / 'PT' -> 'PT': compares languages ignoring region and case."""
    return next((p for p in re.split(r"[-_]", (code or "").upper()) if p), "")
