"""Language table for services with no language list of their own (LLMs).
Codes follow DeepL's spelling (PT-BR, EN-US, ...) so caches and the --target option behave the same way."""
import re

ISO_639_1 = (
    ("aa", "Afar"), ("ab", "Abkhazian"), ("ae", "Avestan"), ("af", "Afrikaans"), ("ak", "Akan"),
    ("am", "Amharic"), ("an", "Aragonese"), ("ar", "Arabic"), ("as", "Assamese"), ("av", "Avaric"),
    ("ay", "Aymara"), ("az", "Azerbaijani"), ("ba", "Bashkir"), ("be", "Belarusian"), ("bg", "Bulgarian"),
    ("bi", "Bislama"), ("bm", "Bambara"), ("bn", "Bengali"), ("bo", "Tibetan"), ("br", "Breton"),
    ("bs", "Bosnian"), ("ca", "Catalan"), ("ce", "Chechen"), ("ch", "Chamorro"), ("co", "Corsican"),
    ("cr", "Cree"), ("cs", "Czech"), ("cu", "Church Slavic"), ("cv", "Chuvash"), ("cy", "Welsh"),
    ("da", "Danish"), ("de", "German"), ("dv", "Dhivehi"), ("dz", "Dzongkha"), ("ee", "Ewe"),
    ("el", "Greek"), ("en", "English"), ("eo", "Esperanto"), ("es", "Spanish"), ("et", "Estonian"),
    ("eu", "Basque"), ("fa", "Persian"), ("ff", "Fulah"), ("fi", "Finnish"), ("fj", "Fijian"),
    ("fo", "Faroese"), ("fr", "French"), ("fy", "Western Frisian"), ("ga", "Irish"),
    ("gd", "Scottish Gaelic"), ("gl", "Galician"), ("gn", "Guarani"), ("gu", "Gujarati"), ("gv", "Manx"),
    ("ha", "Hausa"), ("he", "Hebrew"), ("hi", "Hindi"), ("ho", "Hiri Motu"), ("hr", "Croatian"),
    ("ht", "Haitian Creole"), ("hu", "Hungarian"), ("hy", "Armenian"), ("hz", "Herero"),
    ("ia", "Interlingua"), ("id", "Indonesian"), ("ie", "Interlingue"), ("ig", "Igbo"),
    ("ii", "Sichuan Yi"), ("ik", "Inupiaq"), ("io", "Ido"), ("is", "Icelandic"), ("it", "Italian"),
    ("iu", "Inuktitut"), ("ja", "Japanese"), ("jv", "Javanese"), ("ka", "Georgian"), ("kg", "Kongo"),
    ("ki", "Kikuyu"), ("kj", "Kuanyama"), ("kk", "Kazakh"), ("kl", "Kalaallisut"), ("km", "Khmer"),
    ("kn", "Kannada"), ("ko", "Korean"), ("kr", "Kanuri"), ("ks", "Kashmiri"), ("ku", "Kurdish"),
    ("kv", "Komi"), ("kw", "Cornish"), ("ky", "Kyrgyz"), ("la", "Latin"), ("lb", "Luxembourgish"),
    ("lg", "Ganda"), ("li", "Limburgish"), ("ln", "Lingala"), ("lo", "Lao"), ("lt", "Lithuanian"),
    ("lu", "Luba-Katanga"), ("lv", "Latvian"), ("mg", "Malagasy"), ("mh", "Marshallese"),
    ("mi", "Maori"), ("mk", "Macedonian"), ("ml", "Malayalam"), ("mn", "Mongolian"), ("mr", "Marathi"),
    ("ms", "Malay"), ("mt", "Maltese"), ("my", "Burmese"), ("na", "Nauru"), ("nb", "Norwegian Bokmål"),
    ("nd", "North Ndebele"), ("ne", "Nepali"), ("ng", "Ndonga"), ("nl", "Dutch"),
    ("nn", "Norwegian Nynorsk"), ("no", "Norwegian"), ("nr", "South Ndebele"), ("nv", "Navajo"),
    ("ny", "Chichewa"), ("oc", "Occitan"), ("oj", "Ojibwe"), ("om", "Oromo"), ("or", "Odia"),
    ("os", "Ossetian"), ("pa", "Punjabi"), ("pi", "Pali"), ("pl", "Polish"), ("ps", "Pashto"),
    ("pt", "Portuguese"), ("qu", "Quechua"), ("rm", "Romansh"), ("rn", "Kirundi"), ("ro", "Romanian"),
    ("ru", "Russian"), ("rw", "Kinyarwanda"), ("sa", "Sanskrit"), ("sc", "Sardinian"), ("sd", "Sindhi"),
    ("se", "Northern Sami"), ("sg", "Sango"), ("si", "Sinhala"), ("sk", "Slovak"), ("sl", "Slovenian"),
    ("sm", "Samoan"), ("sn", "Shona"), ("so", "Somali"), ("sq", "Albanian"), ("sr", "Serbian"),
    ("ss", "Swati"), ("st", "Southern Sotho"), ("su", "Sundanese"), ("sv", "Swedish"), ("sw", "Swahili"),
    ("ta", "Tamil"), ("te", "Telugu"), ("tg", "Tajik"), ("th", "Thai"), ("ti", "Tigrinya"),
    ("tk", "Turkmen"), ("tl", "Tagalog"), ("tn", "Tswana"), ("to", "Tongan"), ("tr", "Turkish"),
    ("ts", "Tsonga"), ("tt", "Tatar"), ("tw", "Twi"), ("ty", "Tahitian"), ("ug", "Uyghur"),
    ("uk", "Ukrainian"), ("ur", "Urdu"), ("uz", "Uzbek"), ("ve", "Venda"), ("vi", "Vietnamese"),
    ("vo", "Volapük"), ("wa", "Walloon"), ("wo", "Wolof"), ("xh", "Xhosa"), ("yi", "Yiddish"),
    ("yo", "Yoruba"), ("za", "Zhuang"), ("zh", "Chinese"), ("zu", "Zulu"),
)

# languages that need a variant to be translated into; a bare code/name picks DEFAULT_VARIANT
_VARIANTS = {
    "en": (("EN-GB", "English (British)"), ("EN-US", "English (American)")),
    "pt": (("PT-BR", "Portuguese (Brazilian)"), ("PT-PT", "Portuguese (European)")),
    "zh": (("ZH-HANS", "Chinese (Simplified)"), ("ZH-HANT", "Chinese (Traditional)")),
}
DEFAULT_VARIANT = {"EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS"}

TARGETS = tuple(sorted(item for code, name in ISO_639_1
                       for item in (_VARIANTS.get(code) or ((code.upper(), name),))))
NAMES = {code.upper(): name for code, name in ISO_639_1}        # base code -> name

_BY_CODE = dict(TARGETS)
_BY_NAME = {n.upper(): c for c, n in TARGETS}
_BY_BASE_NAME = {n.upper(): DEFAULT_VARIANT.get(c.upper(), c.upper()) for c, n in ISO_639_1}
_FREE_TEXT = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '()-]){1,39}")


def lookup(raw):
    """Typed code or English name ('pt', 'PT_br', 'basque') -> code in TARGETS, or None."""
    s = " ".join((raw or "").split()).upper().replace("_", "-")
    if s in _BY_CODE:
        return s
    return DEFAULT_VARIANT.get(s) or _BY_NAME.get(s) or _BY_BASE_NAME.get(s)


def free_text(raw):
    """A language name that is not in the table ('Klingon'), cleaned up; None if it looks wrong."""
    s = " ".join((raw or "").split())
    return s.title() if _FREE_TEXT.fullmatch(s) else None