import threading
import time
import re

from ankitool.core.errors import Cancelled


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    @property
    def cancelled(self):
        return self._ev.is_set()
    
    @property
    def event(self):
        return self._ev

class Reporter:
    """Feedback channel for the core. This base is silent (suitable for use as a library);
    the CLI uses ConsoleReporter and the GUI will have its own."""

    def __init__(self, cancel=None):
        self.cancel = cancel or CancelToken()

    def log(self, *a): pass
    def live(self, *a): pass          # a line that is rewritten in place (progress of one long step)
    def warn(self, *a): pass
    def advance(self, label, done, total): pass

    def waiting(self, seconds, reason):
        self.warn(f" {reason}; waiting {seconds}s before retrying...")

    def check_cancelled(self):
        if self.cancel.cancelled:
            raise Cancelled("Cancelled.")

def track(rep, items, label):
    """Iterates over 'items', reporting progress to the Reporter (and honoring cancellation)."""
    total = len(items)
    for i, x in enumerate(items, 1):
        rep.check_cancelled()
        yield x
        rep.advance(label, i, total)

def format_import_progress(text, media_total=0):
    """Anki's import progress text without the invisible bidi marks. When it reports media files and
    the package's media total is known, adds the share: 'Imported 2669 media files of 5729 (46%)'.
    Any other phase (notes, decks without media) is shown as Anki words it."""
    clean = re.sub(r"[\u2066-\u2069]", "", text or "").strip()
    m = re.search(r"(\d+)\s+media", clean, re.I)
    if m and media_total:
        return f"{clean} of {media_total} ({min(100, int(m.group(1)) * 100 // media_total)}%)"
    return clean

class Heartbeat:
    """For long calls with no internal progress (import/export): logs elapsed time every few
    seconds, plus the text from status() when given (e.g. Anki's own progress)."""
    def __init__(self, rep, msg, every=1, status=None):
        self.rep, self.msg, self.every, self.status = rep, msg, every, status

    def __enter__(self):
        self.t0, self.stop = time.time(), threading.Event()
        self.rep.log(self.msg + "...")
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while not self.stop.wait(self.every):
            try:
                info = self.status() if self.status else ""
            except Exception:
                info = ""
            self.rep.live(f"  {self.msg}... {int(time.time() - self.t0)}s" + (f" - {info}" if info else ""))

    def __exit__(self, exc_type, *rest):
        self.stop.set()
        if exc_type is None:
            self.rep.log(f"{self.msg}: finished in {time.time() - self.t0:.1f}s")
        return False