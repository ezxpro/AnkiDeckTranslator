"""Translation cache: one SQLite file (cache.sqlite). Tables and rules: cache_schema.sql.

Scope decides which stored translations a service may reuse (config.json providers.NAME.cache, or per run):
  shared   (default) its own translation first, then any other service's, in a fixed order.
  provider           any model of its own service; nothing from other services.
  isolated           only its exact service + model.
Manual corrections are used in every scope and are never overwritten by a service.
Writes always go to the writing service's own profile.
"""
import sqlite3
import time
from pathlib import Path

from ankitool.core.errors import ConfigError
from ankitool.core.progress import Reporter

MODES = ("shared", "provider", "isolated")
DEFAULT_MODE = "shared"
MANUAL = "manual"
SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE providers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE profiles (
    id INTEGER PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id),
    variant TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'provider' CHECK (kind IN ('provider', 'manual')),
    priority INTEGER NOT NULL DEFAULT 100,
    UNIQUE (provider_id, variant)
);
CREATE TABLE texts (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL UNIQUE,
    lang TEXT,
    lang_source TEXT
);
CREATE TABLE translations (
    text_id INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    profile_id INTEGER NOT NULL REFERENCES profiles(id),
    output TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (text_id, source, target, profile_id)
);
CREATE INDEX translations_by_profile ON translations (profile_id);
INSERT INTO providers (name) VALUES ('manual');
INSERT INTO profiles (provider_id, variant, kind, priority)
    SELECT id, '', 'manual', 0 FROM providers WHERE name = 'manual';
PRAGMA user_version = 1;
"""

# which profiles a service may read, besides manual corrections (always readable)
_SCOPE = {
    "shared": "1",
    "provider": "pr.name = :svc",
    "isolated": "pr.name = :svc AND p.variant = :var",
}


class LangView:
    """Detected language of each plain text. Shared by all services; manual overrides win."""

    def __init__(self, cache):
        self.cache = cache

    def __contains__(self, text):
        return self.cache.conn.execute(
            "SELECT 1 FROM texts WHERE content = ? AND lang IS NOT NULL", (text,)).fetchone() is not None

    def __getitem__(self, text):
        row = self.cache.conn.execute(
            "SELECT lang FROM texts WHERE content = ? AND lang IS NOT NULL", (text,)).fetchone()
        if row is None:
            raise KeyError(text)
        return row[0]

    def __len__(self):
        return self.cache.conn.execute("SELECT COUNT(*) FROM texts WHERE lang IS NOT NULL").fetchone()[0]


class TranslationCache:
    def __init__(self, path, rep=None):
        self.path = Path(path)
        self.rep = rep or Reporter()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = ON")       # needed for ON DELETE CASCADE
        self._pids = {}
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.conn.executescript(_DDL)
        elif version != SCHEMA_VERSION:
            raise ConfigError(f"{self.path.name} has schema version {version}; this version needs "
                              f"{SCHEMA_VERSION}. There is no migration: delete the file to start again.")
        self.lang = LangView(self)

    def save(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- ids (created on write only; reads never create rows) ----
    def _profile_id(self, service, variant=""):
        key = (service, variant)
        if key not in self._pids:
            self.conn.execute("INSERT OR IGNORE INTO providers (name) VALUES (?)", (service,))
            pid = self.conn.execute("SELECT id FROM providers WHERE name = ?", (service,)).fetchone()[0]
            kind = "manual" if service == MANUAL else "provider"
            self.conn.execute("INSERT OR IGNORE INTO profiles (provider_id, variant, kind) VALUES (?, ?, ?)",
                              (pid, variant, kind))
            self._pids[key] = self.conn.execute(
                "SELECT id FROM profiles WHERE provider_id = ? AND variant = ?", (pid, variant)).fetchone()[0]
        return self._pids[key]

    def _text_id(self, content):
        self.conn.execute("INSERT OR IGNORE INTO texts (content) VALUES (?)", (content,))
        return self.conn.execute("SELECT id FROM texts WHERE content = ?", (content,)).fetchone()[0]

    # ---- translations ----
    def get(self, T, L, text, service, variant="", mode=DEFAULT_MODE):
        row = self.conn.execute(
            f"""SELECT t.output
                FROM translations t
                JOIN texts x      ON x.id = t.text_id
                JOIN profiles p   ON p.id = t.profile_id
                JOIN providers pr ON pr.id = p.provider_id
                WHERE x.content = :text AND t.source = :src AND t.target = :tgt
                  AND (p.kind = 'manual' OR ({_SCOPE[mode]}))
                ORDER BY (p.kind = 'manual') DESC,
                         (pr.name = :svc AND p.variant = :var) DESC,
                         (pr.name = :svc) DESC,
                         p.priority, t.created_at, t.profile_id
                LIMIT 1""",
            dict(text=text, src=L, tgt=T, svc=service, var=variant)).fetchone()
        return row[0] if row else None

    def _store(self, T, L, src, out, pid):
        tid = self._text_id(src)
        self.conn.execute(
            "INSERT OR REPLACE INTO translations (text_id, source, target, profile_id, output, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (tid, L, T, pid, out, int(time.time())))

    def put(self, T, L, src, out, service, variant=""):
        if service == MANUAL:
            raise ValueError("services never write manual corrections; use put_manual")
        self._store(T, L, src, out, self._profile_id(service, variant))

    def put_manual(self, T, L, src, out):
        self._store(T, L, src, out, self._profile_id(MANUAL))

    # deck names are texts like any other: same tables, same reuse rules
    def deck_get(self, T, L, seg, service, variant="", mode=DEFAULT_MODE):
        return self.get(T, L or "auto", seg, service, variant, mode)

    def deck_put(self, T, L, seg, out, service, variant=""):
        self.put(T, L or "auto", seg, out, service, variant)

    # ---- detected languages ----
    def set_lang(self, text, lang, source):
        """source = detecting service, or 'manual'. A detector never overwrites a manual language."""
        tid = self._text_id(text)
        self.conn.execute(
            "UPDATE texts SET lang = ?, lang_source = ? WHERE id = ? "
            "AND (lang_source IS NULL OR lang_source <> 'manual' OR ? = 'manual')",
            (lang, source, tid, source))

    # ---- maintenance ----
    def _prune_texts(self):
        self.conn.execute("DELETE FROM texts WHERE lang IS NULL "
                          "AND id NOT IN (SELECT text_id FROM translations)")

    def invalidate(self, service=None, target=None, lang=None, text=None):
        """Deletes translations matching ALL given filters. Manual corrections are kept unless
        service='manual' is named. Returns the number of translations removed."""
        where, args = [], {}
        if service != MANUAL:
            where.append("profile_id NOT IN (SELECT id FROM profiles WHERE kind = 'manual')")
        if service:
            where.append("profile_id IN (SELECT p.id FROM profiles p JOIN providers pr "
                         "ON pr.id = p.provider_id WHERE pr.name = :svc)")
            args["svc"] = service
        if target:
            where.append("target = :tgt")
            args["tgt"] = target
        if lang:
            where.append("source = :src")
            args["src"] = lang
        if text is not None:
            where.append("text_id IN (SELECT id FROM texts WHERE content = :txt)")
            args["txt"] = text
        n = self.conn.execute("DELETE FROM translations WHERE " + " AND ".join(where), args).rowcount
        self._prune_texts()
        return n

    def clear_all(self):
        """Removes all provider translations and detected languages; manual corrections are kept.
        Returns (translations removed, languages removed)."""
        n_tr = self.invalidate()
        n_lang = self.conn.execute(
            "UPDATE texts SET lang = NULL, lang_source = NULL "
            "WHERE lang IS NOT NULL AND (lang_source IS NULL OR lang_source <> 'manual')").rowcount
        self._prune_texts()
        return n_tr, n_lang

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]

    def export_rows(self):
        """One dict per translation (or per text without one). Read-only, for inspection."""
        rows = self.conn.execute(
            """SELECT x.content, x.lang, x.lang_source, t.source, t.target,
                      pr.name, p.variant, t.output, t.created_at
               FROM texts x
               LEFT JOIN translations t ON t.text_id = x.id
               LEFT JOIN profiles p     ON p.id = t.profile_id
               LEFT JOIN providers pr   ON pr.id = p.provider_id
               ORDER BY x.content, t.target, pr.name, p.variant""").fetchall()
        keys = ("text", "detected_lang", "lang_source", "source", "target",
                "service", "variant", "output", "created_at")
        return [dict(zip(keys, r)) for r in rows]

    def stats(self):
        """{service: {target: number of translations}}"""
        out = {}
        rows = self.conn.execute(
            "SELECT pr.name, t.target, COUNT(*) FROM translations t "
            "JOIN profiles p ON p.id = t.profile_id JOIN providers pr ON pr.id = p.provider_id "
            "GROUP BY pr.name, t.target")
        for svc, T, n in rows:
            out.setdefault(svc, {})[T] = n
        return out


class CacheView:
    """What one service may read and write: its profile (service + variant) and its scope."""

    def __init__(self, cache, service, variant="", mode=DEFAULT_MODE):
        if mode not in MODES:
            raise ValueError(f"unknown cache mode: {mode}")
        self.cache, self.service, self.variant, self.mode = cache, service, variant, mode

    @property
    def lang(self):
        return self.cache.lang

    def read(self, T, L, text):
        return self.cache.get(T, L, text, self.service, self.variant, self.mode)

    get = read                                      # used by AnkiCollection.apply_translations

    def write(self, T, L, src, out):
        self.cache.put(T, L, src, out, self.service, self.variant)

    def read_deck(self, T, L, seg):
        return self.cache.deck_get(T, L, seg, self.service, self.variant, self.mode)

    def write_deck(self, T, L, seg, out):
        self.cache.deck_put(T, L, seg, out, self.service, self.variant)

    def save(self):
        self.cache.save()