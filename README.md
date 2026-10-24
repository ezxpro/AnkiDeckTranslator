# Anki Deck Translator (`ankitool`)

A command-line tool that **translates, reverses, and inspects Anki decks** (`.apkg` files)
using any of several machine-translation services. It keeps your card structure intact and
offers an interactive menu, so no command-line experience is required.

- **Translate** card fields (and, optionally, deck names) into another language
- **Reverse** cards — swap what shows on the front and back without editing the text
- **Inspect** a deck — list note types, fields, and decks without creating any file
- Works with **machine-translation** services (**DeepL**, **Azure Translator**) and **LLMs**
  (**Google Gemini**, **Groq**, **OpenAI** or any OpenAI-compatible API, including **Ollama**
  locally), plus a free offline `fake` provider for testing
- **Pick the target language** with the arrow keys, or type its code or name in any case
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

| Variable       | Used by           | Description |
| -------------- | ----------------- | ----------- |
| `DEEPL_KEY`    | DeepL             | DeepL API key (ends in `:fx`) |
| `AZURE_KEY`    | Azure Translator  | Key 1 or Key 2 of the Translator resource (either works) |
| `AZURE_REGION` | Azure Translator  | Region shown next to the key, e.g. `brazilsouth`. Needed for regional resources; ankitool warns if it is missing |
| `GEMINI_KEY`   | Gemini            | Google Gemini key (free tier available) |
| `GROQ_KEY`     | Groq              | Groq key (free tier available) |
| `OPENAI_KEY`   | OpenAI-compatible | Any OpenAI-compatible service |

Optional overrides (defaults built in; all can also go in `config.json`):

| Variable            | Default                          | Purpose |
| ------------------- | -------------------------------- | ------- |
| `ANKITOOL_PROVIDER` | *(asks you)*                     | Service used when `--provider` is not given |
| `AZURE_ENDPOINT`    | `https://api.cognitive.microsofttranslator.com` | Another Azure endpoint (https only) |
| `GEMINI_MODEL`      | `gemini-flash-lite-latest`       | Gemini model |
| `GROQ_MODEL`        | `openai/gpt-oss-20b`             | Groq model |
| `GROQ_BASE_URL`     | `https://api.groq.com/openai/v1` | Groq endpoint |
| `OPENAI_MODEL`      | `gpt-4o-mini`                    | OpenAI-compatible model |
| `OPENAI_BASE_URL`   | `https://api.openai.com/v1`      | e.g. another host, or `http://localhost:11434/v1` (Ollama) |
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

Two kinds of service:

- **Machine translation (MT):** DeepL and Azure Translator. Consistent results, billed per character.
- **LLMs:** Gemini, Groq and OpenAI-compatible APIs (including a local Ollama). Results can differ
  slightly between runs, and any language can be requested.

Choose a provider with `--provider` (or let it default to the configured one):

```
ankitool translate deck.apkg --provider deepl     # needs DEEPL_KEY
ankitool translate deck.apkg --provider azure     # needs AZURE_KEY and AZURE_REGION
ankitool translate deck.apkg --provider gemini    # needs GEMINI_KEY (free tier available)
ankitool translate deck.apkg --provider groq      # needs GROQ_KEY  (free tier available)
ankitool translate deck.apkg --provider openai    # any OpenAI-compatible API: OPENAI_KEY + OPENAI_BASE_URL
```

| Provider | Kind | Key env | Extra needed | Notes |
| -------- | ---- | ------- | ------------ | ----- |
| `deepl`  | MT   | `DEEPL_KEY` | `deepl` | Paid; reports character usage |
| `azure`  | MT   | `AZURE_KEY` + `AZURE_REGION` | *(none, plain HTTP)* | Free tier F0 (2M characters/month); reports characters sent in the run |
| `gemini` | LLM  | `GEMINI_KEY` | `google-genai` (+ `httpx`) | Free tier; results can vary slightly per run |
| `groq`   | LLM  | `GROQ_KEY` | *(none, plain HTTP)* | Generous free tier |
| `openai` | LLM  | `OPENAI_KEY` | *(none, plain HTTP)* | OpenAI-compatible chat API; point `OPENAI_BASE_URL` at Ollama etc. |
| `fake`   | test | *(none)* | *(none)* | No key/network; prefixes the language code. For testing only (use `--provider fake`) |

### Azure Translator

Create a **Translator** resource in the Azure portal (the free F0 tier is enough to start) and copy
a key and the region from *Keys and Endpoint* into `.env`:

```
AZURE_KEY=your-key-here
AZURE_REGION=brazilsouth
```

Key 1 and Key 2 work the same way (two exist so you can rotate one without downtime); use either.
Regional and multi-service resources need `AZURE_REGION`; only a *global* resource works without
it, so ankitool prints a warning at startup when it is empty. `[sound:...]` references and cloze
markers are protected, and large batches are split to stay under Azure's per-request limit.
`429` rate limits are waited out and retried like with the other services.

### LLMs

Gemini is a language model, so results can differ slightly between runs. The model defaults to
`gemini-flash-lite-latest`; set `GEMINI_MODEL` to use another one. Free-tier limits are shown in
[Google AI Studio](https://aistudio.google.com/apikey) and change over time; `429` rate-limit
errors are waited out and retried automatically.

The OpenAI-compatible provider also covers **Groq**, **Cerebras**, **OpenRouter**, and a
**local Ollama** server (e.g. `OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=llama3`).

## Choosing the target language

In the interactive mode a **picker** opens: move with the arrow keys (PgUp/PgDn, Home/End), type
to filter by code or name, Enter to choose, Esc to clear the filter (and, if empty, cancel).
With `-t` or in a plain terminal you type the **code or the name**, in any case: `PT-BR`,
`pt-br`, `French`, `portuguese (brazilian)`. The start of a name also works when only one
language matches (`germ` is German).

| Service | Languages offered |
| ------- | ----------------- |
| DeepL | The service's own list |
| Azure Translator | Azure's own list, fetched at start (a built-in table is used if it cannot be reached) |
| LLMs | A built-in table of ~186 languages (ISO 639-1) with the variants `PT-BR`/`PT-PT`, `EN-GB`/`EN-US`, `ZH-HANS`/`ZH-HANT`. A bare `pt`, `en` or `zh` means `PT-BR`, `EN-US`, `ZH-HANS` |

An LLM also accepts a language **outside the table** (for example `Klingon`): you are asked to
confirm (with `-t` or `-y` only a notice is printed) and the name is sent to the model as typed.
The table only guarantees the language is accepted; quality for rare languages varies. Codes follow
DeepL's spelling in every service, so cached translations stay compatible between them.

## Command reference

Run `ankitool` with no arguments to open the interactive menu, or `ankitool -h` to list the
commands. The main commands:

```
ankitool translate deck.apkg            # translate the card text (and, optionally, deck names)
ankitool reverse  deck.apkg             # swap the front and back of the cards (no translation)
ankitool info      deck.apkg            # see note types, fields and decks (creates no file)
ankitool langs                          # list the languages a service can translate into
ankitool config show                    # see the saved preferences (config.json)
ankitool cache                          # see how each service reuses saved translations
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
| `cache`     | Show, change or export which saved translations each service reuses; delete some or all | *(no file, or the export file)* |

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
| `-t, --target CODE` | Target language: code or name, e.g. `PT-BR`, `french` (see `langs` and [Choosing the target language](#choosing-the-target-language)) |
| `-l, --lang CODES` | Source language(s) to translate, e.g. `IT,EN` or `all` |
| `-d, --rename-decks none\|main\|all` | Rename deck names too: unchanged / top-level / all |
| `-m, --deck-method auto\|manual\|review\|keep` | How to rename decks |
| `--deck-name CURRENT=NEW` | Rename a specific deck (repeatable) |
| `--dry-run` | Estimate texts/chars only; no file created |
| `--field FIELD` / `--skip-field FIELD` | Translate only / not a field (or `NOTETYPE:FIELD`) |
| `--sample N` | Texts per field sampled for detection (default 10) |
| `--min-ratio R` | Min agreement fraction (0–1) to trust detection (default 0.6) |
| `--cache-mode shared\|provider\|isolated` | Which saved translations are reused (see [Caching](#caching)); default: the service's setting, or `shared` |
| `--cache-dir FOLDER` | Folder for cache files (default: script folder) |
| `-o, --output OUT` | Output file. `.apkg` is added when missing (`deck.apkg.bak` → `deck.apkg.bak.apkg`); a repeated `deck.apkg.apkg` becomes `deck.apkg` |
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

### `cache`

```
ankitool cache                                   # show each service's mode and stored count
ankitool cache use SERVICE [shared|provider|isolated]   # show or set one service's mode
ankitool cache use SERVICE --reset               # back to shared
ankitool cache select                            # choose service and mode from a menu
ankitool cache export FILE                       # write the whole cache to a JSON file
ankitool cache invalidate -p SERVICE [-t CODE] [--lang CODE] [--text TEXT] [-y]   # delete some translations
ankitool cache invalidate --all [-y]             # delete all translations and text languages
```

`invalidate` deletes the saved translations that match **all** the filters you give.
Text languages are kept, except with `--all`. It asks first unless `-y` is given. Deleted texts are sent
to the service again next time they are translated.

The mode is saved in `config.json` per service (`providers.SERVICE.cache`). It can also be set with
`ankitool config set SERVICE.cache MODE`. The interactive menu has the same choice under
"Translation cache". See [Caching](#caching) for what each mode does.

## How it works

The translate pipeline: **import → detect → translate → (optional) rename → export**.

1. **Import** the `.apkg` using the `anki` library (the only module that imports it). The log shows
   how many media files the package has and, while importing, one line rewritten in place once a
   second, e.g. `Imported 2669 media files of 5729 (46%)` (decks without media show Anki's own text).
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

Translations are stored in **one SQLite file, `cache.sqlite`**, in the script's folder (or
`--cache-dir FOLDER`). It has four tables:

| Table | Holds |
| ----- | ----- |
| `texts` | each distinct text once, with its detected language |
| `providers` | the service names (DeepL, Gemini, ...) |
| `profiles` | a service and model pair, such as `gemini` with its model |
| `translations` | one row per text, source language, target language and profile |

Each text is stored once, however many decks, fields or services use it. Deck names are stored the
same way, so changing one line of a deck only sends that line again. Each service and model keeps
its own translations, so services never overwrite each other.

Which saved translations a service may reuse is set by its **mode**:

| Mode | Reuses |
| ---- | ------ |
| `shared` (default) | its own translation first, then any other service's |
| `provider` | any model of its own service; nothing from other services |
| `isolated` | only its exact service and model |

`ankitool cache export FILE` writes the whole cache to a JSON file, one row per translation, to
inspect it. Delete `cache.sqlite` for a clean start. Older versions used `cache.json`, which is no
longer read.

## Command help

Every command and option is documented in its own `-h` (or `--help`) output. These are the same texts, generated from the program. On a terminal the help is colored; set `NO_COLOR=1` to turn colors off.

### `ankitool -h`

```
usage: ankitool [-h] [-V] COMMAND ...

Tools for Anki .apkg decks. With no arguments, opens the interactive mode
(menu). Use 'COMMAND -h' to see each command's options.

positional arguments:
  COMMAND
    translate    translate fields (and optionally deck names)
    reverse      swap front and back of cards (no translation)
    info         shows note types, fields and decks of an .apkg
    langs        list the translation service's languages (target or source)
    bench        measures a service's speed and rate limits (makes real API
                 calls)
    cache        translation cache: which saved translations each service
                 reuses
    config       show or change the saved preferences (config.json)

options:
  -h, --help     show this help message and exit
  -V, --version  show program's version number and exit

For any command, whatever is not given as an option is asked for, unless
-y/--yes is used.
```

### `ankitool translate -h`

```
usage: ankitool translate [-h] [-y] [-q] [-o OUTPUT] [-f] [--keep-scheduling] [--legacy | --no-legacy] [-p SERVICE] [-t CODE] [-l CODES]
                          [-d {none,main,all}] [-m {auto,manual,review,keep}] [--deck-name CURRENT=NEW] [--cache-mode MODE] [--dry-run]
                          [--field FIELD] [--skip-field FIELD] [--sample N] [--min-ratio R] [--cache-dir FOLDER]
                          [INPUT]

Translates the fields of a deck with a translation service (--provider). Default output: <input>_out.apkg. With -y: PT-BR, all detected
languages, deck names unchanged.

positional arguments:
  INPUT                 input .apkg file

options:
  -h, --help            show this help message and exit
  -y, --yes             don't ask anything: use each command's defaults
  -q, --quiet           only show warnings and the final result (no progress messages)
  -o, --output OUTPUT   output .apkg file
  -f, --force           overwrite the output file if it already exists (otherwise, with -y, the program stops with an error)
  --keep-scheduling     keep study history (dates, intervals) and deck options in the generated file (default: removed)
  --legacy, --no-legacy
                        legacy .apkg format, accepted by any Anki/AnkiDroid version (default); --no-legacy writes the newer, smaller
                        format
  -p, --provider SERVICE
                        translation service: azure, deepl, fake, gemini, groq, openai (default: deepl)
  -t, --target CODE     target language, e.g. PT-BR, EN-US (see the 'langs' command)
  -l, --lang CODES      source language(s) to translate, comma-separated (e.g. IT,EN) or 'all' for all detected ones
  -d, --rename-decks {none,main,all}
                        deck names: none = leave unchanged, main = top-level decks only, all = top-level and sub-decks
  -m, --deck-method {auto,manual,review,keep}
                        how to rename decks: auto (by the service), manual (you type), review (service suggestion, one by one) or keep
                        (only --deck-name ones)
  --deck-name CURRENT=NEW
                        renames the deck named CURRENT (one level, no '::') to NEW; can be repeated. Without -d/-m, only these are
                        renamed
  --cache-mode MODE     which saved translations are reused: shared = any service's (this service's first, then another's); provider =
                        any model of this service; isolated = only this service and model. Default: the one set in config.json
                        (providers.SERVICE.cache), or shared
  --dry-run             only calculates how many texts and characters would be translated; does not translate or create a file (language
                        detection still uses some characters)
  --field FIELD         translate ONLY this field (or NOTETYPE:FIELD); can be repeated
  --skip-field FIELD    do NOT translate this field (or NOTETYPE:FIELD); can be repeated
  --sample N            how many texts from each field to use for language detection (default: 10)
  --min-ratio R         minimum fraction (0 to 1) of the sample that must agree on the language for the field to be considered reliable
                        (default: 0.6)
  --cache-dir FOLDER    folder for the cache files (default: the script's folder)
```

### `ankitool reverse -h`

```
usage: ankitool reverse [-h] [-y] [-q] [-o OUTPUT] [-f] [--keep-scheduling]
                         [--legacy | --no-legacy] [--notetype NAME]
                         [--swap FIELD1=FIELD2]
                         [INPUT]

Swaps the front and back of the cards by exchanging the fields in the card
templates. The text is not changed, only which side each field shows on.
Default output: <input>_reversed.apkg. Running it again on the result undoes
the change. With -y: all note types (cloze is excluded). Use 'info' first to
see the fields.

positional arguments:
  INPUT                 input .apkg file

options:
  -h, --help            show this help message and exit
  -y, --yes             don't ask anything: use each command's defaults
  -q, --quiet           only show warnings and the final result (no progress
                        messages)
  -o OUTPUT, --output OUTPUT
                        output .apkg file
  -f, --force           overwrite the output file if it already exists
                        (otherwise, with -y, the program stops with an error)
  --keep-scheduling     keep study history (dates, intervals) and deck options
                        in the generated file (default: removed)
  --legacy, --no-legacy
                        legacy .apkg format, accepted by any Anki/AnkiDroid
                        version (default); --no-legacy writes the newer,
                        smaller format
  --notetype NAME       swap only this note type (can be repeated)
  --swap FIELD1=FIELD2  pair of fields that switch sides; can be repeated
                        (default: pairs suggested for each note type; see the
                        'info' command)

examples:
  ankitool reverse deck.apkg                       every note type, suggested pairs
  ankitool reverse deck.apkg --notetype Basic --swap Front=Back
  ankitool reverse deck.apkg -o flipped.apkg -f    overwrite flipped.apkg if it exists
```

### `ankitool info -h`

```
usage: ankitool info [-h] [-y] [-q] [INPUT]

Shows what is inside an .apkg: each note type with its fields and how many
notes use it, the front/back split and the suggested --swap pairs, and the
deck tree. Useful before 'translate --field' or 'reverse --swap'. Creates no
file and needs no API key.

positional arguments:
  INPUT        input .apkg file

options:
  -h, --help   show this help message and exit
  -y, --yes    don't ask anything: use each command's defaults
  -q, --quiet  only show warnings and the final result (no progress messages)

examples:
  ankitool info deck.apkg
```

### `ankitool langs -h`

```
usage: ankitool langs [-h] [-p SERVICE] [--source]

Lists the languages a translation service accepts, as code and name. By
default the TARGET languages (what you can translate into). With --source, the
languages it can detect. Codes are what -t and --lang expect. Needs the
service's API key.

options:
  -h, --help            show this help message and exit
  -p SERVICE, --provider SERVICE
                        translation service (default: deepl)
  --source              list SOURCE languages (the ones the service
                        recognizes) instead of the target ones

examples:
  ankitool langs -p deepl
  ankitool langs -p gemini --source
```

### `ankitool bench -h`

```
usage: ankitool bench [-h] [-y] [-q] [-p SERVICE] [-t CODE] [--calls N]
                       [--batch N] [--workers LIST]

Measures how fast a service is and where it starts to refuse requests. It
makes real API calls with 1, 2, 4... parallel workers and reports speed and
errors (rate limits), then suggests a max_workers value to save in
config.json. It uses your API quota, so run it sparingly.

options:
  -h, --help            show this help message and exit
  -y, --yes             don't ask anything: use each command's defaults
  -q, --quiet           only show warnings and the final result (no progress
                        messages)
  -p SERVICE, --provider SERVICE
                        translation service (default: deepl)
  -t CODE, --target CODE
                        target language (default: the service's default)
  --calls N             API calls per worker count (default: 8)
  --batch N             texts per call (default: 10)
  --workers LIST        worker counts to test, comma-separated (default:
                        1,2,4,8)

examples:
  ankitool bench -p gemini
  ankitool bench -p deepl --calls 4 --batch 20 --workers 1,2
```

### `ankitool config -h`

```
usage: ankitool config [-h] [-y] [-q] [{show,set,unset}] [KEY] [VALUE]

Saved preferences in config.json: the default service, and per-service model,
base_url, max_workers and cache mode. API keys are never stored here (they
stay in .env or environment variables). Lookup order: command-line flag, then
environment/.env, then config.json, then the built-in default. With no action
it shows the settings.

positional arguments:
  {show,set,unset}  what to do: show (default), set, or unset a preference
  KEY               provider, or SERVICE.model / SERVICE.base_url /
                    SERVICE.max_workers / SERVICE.cache
  VALUE             new value (for 'set')

options:
  -h, --help        show this help message and exit
  -y, --yes         don't ask anything: use each command's defaults
  -q, --quiet       only show warnings and the final result (no progress
                    messages)

keys:
  provider                       default service (deepl, gemini, groq, openai)
  SERVICE.model                  model name for that service
  SERVICE.base_url               API address (https only, except localhost)
  SERVICE.max_workers            parallel requests (whole number >= 1)
  SERVICE.cache                  cache mode: shared, provider or isolated
examples:
  ankitool config show
  ankitool config set provider groq
  ankitool config set gemini.cache isolated
  ankitool config unset provider
```

### `ankitool cache -h`

```
usage: ankitool cache [-h] [-y] [-q] [--cache-dir FOLDER] ACTION ...

Shows or changes how each service reuses the translations already in
cache.sqlite. shared (default) = reuse any service's translation when its own
is missing; provider = any model of its own service only; isolated = only its
exact service and model. Set per service, or per run with translate --cache-
mode.

positional arguments:
  ACTION
    show              show each service's mode and stored count
    select            choose service and mode from a menu
    export            write the cache to a JSON file
    invalidate        delete saved translations (by service, language or
                      text), or everything
    use               set (or show) one service's mode: shared, provider or
                      isolated

options:
  -h, --help          show this help message and exit
  -y, --yes           don't ask anything: use each command's defaults
  -q, --quiet         only show warnings and the final result (no progress
                      messages)
  --cache-dir FOLDER  folder of cache.sqlite (default: the script's folder)

examples:
  ankitool cache                       show each service's mode
  ankitool cache use deepl isolated    DeepL reuses only its own translations
  ankitool cache use deepl --reset     DeepL back to shared
  ankitool cache select                choose from a menu (interactive)
```

### `ankitool cache show -h`

```
usage: ankitool cache show [-h] [-y] [-q] [--cache-dir FOLDER]

Shows the cache file, the reuse mode of each service (from config.json, or the
default shared), how many translations each service has stored, and the
totals. Reads only; no API calls.

options:
  -h, --help          show this help message and exit
  -y, --yes           don't ask anything: use each command's defaults
  -q, --quiet         only show warnings and the final result (no progress
                      messages)
  --cache-dir FOLDER  folder of cache.sqlite (default: the script's folder)

example:
  ankitool cache show
```

### `ankitool cache export -h`

```
usage: ankitool cache export [-h] [-y] [-q] [--cache-dir FOLDER] FILE

Writes every saved text, its detected language and each saved translation
(service, model, output) to a JSON file, for inspection. Reads only; no API
calls.

positional arguments:
  FILE                JSON file to write (asks before replacing one)

options:
  -h, --help          show this help message and exit
  -y, --yes           don't ask anything: use each command's defaults
  -q, --quiet         only show warnings and the final result (no progress
                      messages)
  --cache-dir FOLDER  folder of cache.sqlite (default: the script's folder)

examples:
  ankitool cache export cache.json
  ankitool cache export ~/Desktop/cache.json
```

### `ankitool cache use -h`

```
usage: ankitool cache use [-h] [-y] [-q] [--cache-dir FOLDER] [--reset] [SERVICE] [MODE]

Sets the reuse mode of one service, saved in config.json. Without MODE it shows the current one. --reset goes back to shared.

positional arguments:
  SERVICE             translation service, e.g. deepl, gemini, groq, openai
  MODE                shared, provider or isolated (omit to show the current mode)

options:
  -h, --help          show this help message and exit
  -y, --yes           don't ask anything: use each command's defaults
  -q, --quiet         only show warnings and the final result (no progress messages)
  --cache-dir FOLDER  folder of cache.sqlite (default: the script's folder)
  --reset             remove the saved mode (back to shared)

examples:
  ankitool cache use deepl isolated
  ankitool cache use gemini
  ankitool cache use gemini --reset
```

### `ankitool cache select -h`

```
usage: ankitool cache select [-h] [-y] [-q] [--cache-dir FOLDER]

Interactive: first pick the service, then its mode (shared or isolated). Saved
to config.json. Same as 'cache use SERVICE MODE', without typing it. Needs a
terminal (not -y).

options:
  -h, --help          show this help message and exit
  -y, --yes           don't ask anything: use each command's defaults
  -q, --quiet         only show warnings and the final result (no progress
                      messages)
  --cache-dir FOLDER  folder of cache.sqlite (default: the script's folder)

example:
  ankitool cache select
```

### `ankitool cache invalidate -h`

```
usage: ankitool cache invalidate [-h] [-y] [-q] [--cache-dir FOLDER]
                                  [-p SERVICE] [-t CODE] [--lang CODE]
                                  [--text TEXT] [--all]

Deletes the saved translations that match ALL the filters given. Text
languages are kept. Asks first unless -y. Texts deleted this way are sent to
the service again next time.

options:
  -h, --help            show this help message and exit
  -y, --yes             don't ask anything: use each command's defaults
  -q, --quiet           only show warnings and the final result (no progress
                        messages)
  --cache-dir FOLDER    folder of cache.sqlite (default: the script's folder)
  -p SERVICE, --provider SERVICE
                        only this service's translations
  -t CODE, --target CODE
                        only this target language, e.g. PT-BR
  --lang CODE           only this source language, e.g. IT
  --text TEXT           only this original text
  --all                 delete all translations and text languages (manual
                        corrections are kept)

examples:
  ankitool cache invalidate -p gemini              all of Gemini's
  ankitool cache invalidate -p gemini -t PT-BR --lang IT
  ankitool cache invalidate --text "ciao"          one text, all services
  ankitool cache invalidate --all -y               empty the cache
```

## Project layout

```
src/ankitool/
  core/        errors, constants, paths, progress (Reporter, CancelToken), languages (LLM language table), text helpers   (no I/O to the user)
  providers/   base.py (contract + registry), deepl.py, azure.py, llm.py (shared LLM base), gemini.py, openai_compat.py, fake.py
  cache/       translation.py: the one cache.sqlite for all services, its modes and views
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