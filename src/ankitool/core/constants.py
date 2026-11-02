SAMPLE, MIN_RATIO = 10, 0.6

DEFAULT_PROVIDER = "deepl"

SEP = "::"

VERSION = "0.0.0.dev0"   # placeholder; CI stamps the release version from the git tag

MIN_DETECT_CHARS = 12      # shorter texts are too ambiguous to detect reliably

AUDIO_MAX_CHARS = 400      # audio: longer texts are skipped (cost; not a word or a sentence)
