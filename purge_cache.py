"""Removes bad machine translations from cache.sqlite (run from the folder that holds it).
Dry run by default; add --apply to delete (a cache.sqlite.bak copy is made first).
Bad = damaged HTML/markers, or a real sentence (4+ words) returned unchanged. Manual
corrections are never touched."""
import shutil
import sqlite3
import sys

from ankitool.core.text import TextTools

DB = "cache.sqlite"
con = sqlite3.connect(DB)
rows = con.execute("""SELECT tr.rowid, t.content, tr.output, p.variant
                      FROM translations tr
                      JOIN texts t ON t.id = tr.text_id
                      JOIN profiles p ON p.id = tr.profile_id
                      WHERE p.kind = 'provider'""").fetchall()
bad = []
for rid, src, out, variant in rows:
    damaged = TextTools.shape(out) != TextTools.shape(src)
    unchanged = out.strip() == src.strip() and len(TextTools.plain(src).split()) >= 4
    if damaged or unchanged:
        bad.append((rid, variant, "damaged" if damaged else "unchanged"))
print(f"{len(bad)} bad of {len(rows)} translations")
for key in sorted({(v, k) for _, v, k in bad}):
    print(" ", key[0] or "(no model)", key[1], sum(1 for _, v, k in bad if (v, k) == key))
if "--apply" in sys.argv and bad:
    con.close()
    shutil.copy(DB, DB + ".bak")
    con = sqlite3.connect(DB)
    con.executemany("DELETE FROM translations WHERE rowid = ?", [(r,) for r, _, _ in bad])
    con.commit()
    print("deleted; backup in", DB + ".bak")
elif bad:
    print("dry run: nothing deleted. Run again with --apply.")