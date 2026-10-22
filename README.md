# Anki-Deck-Automatic-Translator
Uses DeepL free API to translate anki decks


## Install and run

```
pip install -e .[deepl,gemini,dev]     # from the project root (pick the services you use)
ankitool translate deck.apkg    # or: python -m ankitool ...   or: python AnkiTranslator.py ...
pytest
```

Run `ankitool` with no arguments to get the interactive menu, or `ankitool -h` to list the
commands. The main ones:

```
ankitool translate deck.apkg            # translate the card text (and, optionally, deck names)
ankitool reverse  deck.apkg             # swap the front and back of the cards (no translation)
ankitool info      deck.apkg            # see note types, fields and decks (creates no file)
ankitool langs                          # list the languages a service can translate into
ankitool config show                    # see the saved preferences (config.json)
ankitool bench                          # measure a service's speed / rate limits (real API calls)
```

## Layout

```
src/ankitool/
  core/        errors, constants, paths, progress (Reporter, CancelToken), text helpers   (no I/O to the user)
  providers/   base.py (contract + registry), deepl.py, gemini.py, fake.py  -> one file per translation API
  cache/       translation cache, deck-name cache
  anki/        collection.py: the only module that imports the `anki` package
  ops/         translate / rename / reverse: config dataclasses + operations, no print/input
  cli/         console (Ui, ConsoleReporter, file picker), per-command apps, args, main
```

Dependency direction: `core <- providers, cache <- anki <- ops <- cli` (and later `gui`).
Nothing below `cli/` may import `cli/` or call `print`/`input`.
To add a translation service: create `providers/<name>.py` with a `@register_provider` class
and import it in `providers/__init__.py`.

## Services

```
ankitool translate deck.apkg --provider deepl     # needs DEEPL_KEY
ankitool translate deck.apkg --provider gemini    # needs GEMINI_KEY (free tier available)
ankitool translate deck.apkg --provider groq      # needs GROQ_KEY (free tier available)
ankitool translate deck.apkg --provider openai    # any OpenAI-compatible API: OPENAI_KEY + OPENAI_BASE_URL
```

Keys and overrides go in `.env` (see `.env.example`) or in environment variables. Non-secret
preferences live in `config.json`, managed with `ankitool config set provider groq`,
`ankitool config set groq.max_workers 3` and `ankitool config show`. Lookup order: command-line
flag, environment variable / `.env`, `config.json`, built-in default.

Gemini is a language model, so results can differ slightly between runs. The model defaults to
`gemini-flash-lite-latest`; set `GEMINI_MODEL` to use another one. Free-tier limits are shown in
Google AI Studio and change over time; 429 errors are waited out and retried automatically.
