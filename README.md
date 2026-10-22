# Anki Deck Translator (`ankitool`)

A command-line tool that **translates, reverses, and inspects Anki decks** (`.apkg` files)
using any of several machine-translation services. It keeps your card structure intact and
offers an interactive menu, so no command-line experience is required.

- **Translate** card fields (and, optionally, deck names) into another language
- **Reverse** cards — swap what shows on the front and back without editing the text
- **Inspect** a deck — list note types, fields, and decks without creating any file
- Works with **DeepL**, **Google Gemini**, **Groq**, **OpenAI** or any OpenAI-compatible API
  (including **Ollama** locally), plus a free offline `fake` provider for testing
- **Caches translations**, so re-running the same deck costs nothing and is instant
- **Detects languages** automatically, with retries and respects rate limits

---

## Requirements

- Python **3.9** or newer
- The `anki` package (installed automatically)
- Optional, depending on the translation service you use (see [Services](#services))

The only non-stdlib dependencies are the ones you can `pip install`; everything else the
code uses (e.g. `argparse`, `json`, `urllib`, `pathlib`) ships with Python itself.

## Installation

From the project root, install with the extras for the services you use:

```
pip install -e .[deepl,gemini,dev]        # pick deepl / gemini / dev; drop the ones you don't use
```

This installs the `ankitool` command and, via `[dev]`, the test tooling.

Alternatively, install everything at once from the requirements file:

```
pip install -r requirements.txt           # runtime + all providers + pytest
```

`requirements.txt` lists every *pip-installable* package the project uses (no stdlib
modules); `pyproject.toml` remains the source of truth for packaging, with
`requirements.txt` provided as a convenience. Note the Gemini provider imports `httpx`
directly, so it is listed explicitly.

## Quick start

You can run the tool two ways — they are equivalent:

```
ankitool translate deck.apkg              # after `pip install -e .`
python -m ankitool translate deck.apkg    # no install needed (works from a checkout)
```

On Windows, use `py -m ankitool ...` instead of `python -m ankitool ...` if `python` is not on
your PATH.

Run `ankitool` with **no arguments** to get the interactive menu, which prompts you through
everything step by step. Use `ankitool -h` for an overview, or `ankitool COMMAND -h` for a
single command's options.

A minimal one-shot translation:

```
ankitool translate deck.apkg --provider gemini -t PT-BR -y
```

This reads `deck.apkg`, translates into Brazilian Portuguese using the default
(`gemini`/`groq`/`deepl`) model, skips all prompts, and writes `deck_out.apkg`.

## Configuration

Settings split into two places: **secrets** and **non-secret preferences**.

### API keys — `.env`

Copy `.env.example` to `.env` next to the script and fill in only the services you use
(`.env` is git-ignored). Keys can also be ordinary environment variables, which take priority.

| Variable     | Used by           | Description |
| ------------ | ----------------- | ----------- |
| `DEEPL_KEY`  | DeepL             | DeepL API key (ends in `:fx`) |
| `GEMINI_KEY` | Gemini            | Google Gemini key (free tier available) |
| `GROQ_KEY`   | Groq              | Groq key (free tier available) |
| `OPENAI_KEY` | OpenAI-compatible | Any OpenAI-compatible service |

Optional overrides (defaults built in; all can also go in `config.json`):

| Variable           | Default                       | Purpose |
| ------------------ | ----------------------------- | ------- |
| `ANKITOOL_PROVIDER` | *(asks you)*                  | Service used when `--provider` is not given |
| `GEMINI_MODEL`     | `gemini-flash-lite-latest`    | Gemini model |
| `GROQ_MODEL`       | `openai/gpt-oss-20b`          | Groq model |
| `GROQ_BASE_URL`    | `https://api.groq.com/openai/v1` | Groq endpoint |
| `OPENAI_MODEL`     | `gpt-4o-mini`                 | OpenAI-compatible model |
| `OPENAI_BASE_URL`  | `https://api.openai.com/v1`   | e.g. another host, or `http://localhost:11434/v1` (Ollama) |

### Preferences — `config.json`

Non-secret preferences (default provider, model, worker count) live in `config.json`, managed
with the `config` command:

```
ankitool config set provider groq
ankitool config set groq.max_workers 3
ankitool config show            # see the saved preferences
ankitool config unset provider
```

### Lookup order

For every value: **command-line flag → environment variable / `.env` → `config.json` →
built-in default**.

## Services

Choose a provider with `--provider` (or let it default to the configured one):

```
ankitool translate deck.apkg --provider deepl     # needs DEEPL_KEY
ankitool translate deck.apkg --provider gemini    # needs GEMINI_KEY (free tier available)
ankitool translate deck.apkg --provider groq      # needs GROQ_KEY  (free tier available)
ankitool translate deck.apkg --provider openai    # any OpenAI-compatible API: OPENAI_KEY + OPENAI_BASE_URL
```

| Provider | Key env | Extra needed | Notes |
| -------- | ------- | ------------ | ----- |
| `deepl`  | `DEEPL_KEY` | `deepl` | Paid; reports character usage |
| `gemini` | `GEMINI_KEY` | `google-genai` (+ `httpx`) | Google Gemini LLM; free tier; results can vary slightly per run |
| `groq`   | `GROQ_KEY`  | *(none — plain HTTP)* | Groq LLM; generous free tier |
| `openai` | `OPENAI_KEY` | *(none — plain HTTP)* | OpenAI-compatible chat API; point `OPENAI_BASE_URL` at Ollama etc. |
| `fake`   | *(none)*    | *(none)* | No key/network; prefixes the language code. For testing only (use `--provider fake`) |

Gemini is a language model, so results can differ slightly between runs. The model defaults to
`gemini-flash-lite-latest`; set `GEMINI_MODEL` to use another one. Free-tier limits are shown in
[Google AI Studio](https://aistudio.google.com/apikey) and change over time; `429` rate-limit
errors are waited out and retried automatically.

The OpenAI-compatible provider also covers **Groq**, **Cerebras**, **OpenRouter**, and a
**local Ollama** server (e.g. `OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=llama3`).



## Command reference

Run `ankitool` with no arguments to open the interactive menu, or `ankitool -h` to list the
commands. The main commands:

```
ankitool translate deck.apkg            # translate the card text (and, optionally, deck names)
ankitool reverse  deck.apkg             # swap the front and back of the cards (no translation)
ankitool info      deck.apkg            # see note types, fields and decks (creates no file)
ankitool langs                          # list the languages a service can translate into
ankitool config show                    # see the saved preferences (config.json)
ankitool bench                          # measure a service's speed / rate limits (real API calls)
```

| Command | Purpose | Default output |
| ------- | ------- | -------------- |
| `translate` | Translate fields (and optionally deck names) | `<input>_out.apkg` |
| `reverse`   | Swap front/back of cards (no translation) | `<input>_reversed.apkg` |
| `info`      | Show note types, fields, and decks | *(no file)* |
| `langs`     | List a service's target (or `--source`) languages | *(no file)* |
| `bench`     | Measure a service's speed/rate limits (real API calls) | *(no file)* |
| `config`    | Show/change saved preferences (`config.json`) | *(no file)* |

Common flags on most commands: `-y/--yes` (don't ask, use defaults), `-q/--quiet` (only
warnings and the final result). Any option you don't provide and isn't defaulted will be asked
for interactively — unless `-y` is given.

### `translate`

```
ankitool translate deck.apkg [options]
```

| Flag | Description |
| ---- | ----------- |
| `-p, --provider SERVICE` | Translation service |
| `-t, --target CODE` | Target language, e.g. `PT-BR`, `EN-US` (see `langs`) |
| `-l, --lang CODES` | Source language(s) to translate, e.g. `IT,EN` or `all` |
| `-d, --rename-decks none\|main\|all` | Rename deck names too: unchanged / top-level / all |
| `-m, --deck-method auto\|manual\|review\|keep` | How to rename decks |
| `--deck-name CURRENT=NEW` | Rename a specific deck (repeatable) |
| `--dry-run` | Estimate texts/chars only; no file created |
| `--field FIELD` / `--skip-field FIELD` | Translate only / not a field (or `NOTETYPE:FIELD`) |
| `--sample N` | Texts per field sampled for detection (default 10) |
| `--min-ratio R` | Min agreement fraction (0–1) to trust detection (default 0.6) |
| `--cache-dir FOLDER` | Folder for cache files (default: script folder) |
| `-o, --output OUT` | Output file |
| `-f, --force` | Overwrite an existing output file |
| `--keep-scheduling` | Keep study history/deck options (default: removed) |
| `--no-legacy` | Write the newer, smaller `.apkg` format |

With `-y`: target `PT-BR`, translate all detected languages, deck names unchanged.

### `reverse`

```
ankitool reverse deck.apkg [--notetype NAME] [--swap FIELD1=FIELD2]
```

Swaps front/back by exchanging fields in the card templates (cloze types are excluded).
Running it again undoes the change. `--swap` specifies which field pairs switch sides; `info`
suggests pairs for each note type.

### `info`

```
ankitool info deck.apkg
```

Shows note types (with field lists and suggested `--swap` pairs) and the deck tree. Creates no
file — useful for deciding `--notetype` and `--swap`.

### `langs`

```
ankitool langs [--provider SERVICE] [--source]
```

Lists the languages a service recognizes as code + name. Defaults to *target* languages (what
you can translate into); `--source` lists the languages it can detect.

### `bench`

```
ankitool bench [--provider SERVICE] [--calls N] [--batch N] [--workers 1,2,4,8]
```

Times the provider with 1, 2, 4, … parallel workers, reports speed and errors (rate limits),
and **suggests** a `max_workers` value. Makes real API calls, so it uses quota.

### `config`

```
ankitool config                       # same as 'config show'
ankitool config show
ankitool config set provider groq
ankitool config set groq.max_workers 3
ankitool config unset provider
```

Keys are `provider` or `SERVICE.model` / `SERVICE.base_url` / `SERVICE.max_workers`. No API
keys are stored here — those stay in `.env`. With no action it defaults to `show`.

## How it works

The translate pipeline: **import → detect → translate → (optional) rename → export**.

1. **Import** the `.apkg` using the `anki` library (the only module that imports it).
2. **Detect** the language of each field by sampling a few cards; fields already in the target
   language (or whose detection is uncertain) are left untouched, so you never pay to translate
   text that's already correct.
3. **Translate** in batches (see each provider's `max_workers`), retrying transient and `429`
   rate-limit errors automatically.
4. **Rename** decks if requested.
5. **Export** to a new `.apkg` — your original file is never modified.

`[sound:...]` references and cloze markers like `{{c1::...}}` are protected (restored after
translation) so they aren't mangled.

## Caching

Translated texts are stored in `cache.json` (or `cache_<service>.json`), and deck-name
translations in `deck_cache.json`. Re-running a translation reuses these, so identical text is
never sent to (or billed by) the service twice. Use `--cache-dir FOLDER` to relocate them; the
cache files are safe to delete if you want a clean start.

## Project layout

```
src/ankitool/
  core/        errors, constants, paths, progress (Reporter, CancelToken), text helpers   (no I/O to the user)
  providers/   base.py (contract + registry), deepl.py, gemini.py, openai_compat.py, fake.py
  cache/       translation cache, deck-name cache
  anki/        collection.py: the only module that imports the `anki` package
  ops/         translate / rename / reverse / bench: config dataclasses + operations, no print/input
  cli/         console (Ui, ConsoleReporter, file picker), per-command apps, args, main
```

Dependency direction: `core ← providers, cache ← anki ← ops ← cli` (and a future `gui`).
Nothing below `cli/` may import `cli/` or call `print`/`input`. To add a translation service,
create `providers/<name>.py` with a `@register_provider` class and import it in
`providers/__init__.py`.

## Development

```
pip install -e .[deepl,gemini,dev]   # or: pip install -r requirements.txt
pytest                              # run the test suite
```

Tests live in `tests/` and use stub/fake clients where possible, so they don't require API keys
or network access (provider config tests isolate themselves from `.env`).

## License

[GPL-3.0](LICENSE)

