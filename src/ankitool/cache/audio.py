"""Generated audio: one file per (service, voice, text) in a folder, so a repeated text is never
synthesized (or billed) twice and an interrupted run resumes where it stopped."""
import hashlib
import os
from pathlib import Path


class AudioCache:
    def __init__(self, folder):
        self.dir = Path(folder)

    @staticmethod
    def filename(service, voice, text, ext="mp3"):
        """Stable name; it is also the name of the media file inside the deck."""
        digest = hashlib.sha1(f"{service}\0{voice}\0{text}".encode("utf-8")).hexdigest()[:20]
        return f"ankitool_{digest}.{ext}"

    def path(self, name):
        return self.dir / name

    def has(self, name):
        p = self.path(name)
        return p.is_file() and p.stat().st_size > 0

    def read(self, name):
        return self.path(name).read_bytes()

    def write(self, name, data):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.path(name))               # never leaves a half-written file
