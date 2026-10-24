import threading
import time

from ankitool.core.errors import Cancelled


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    @property
    def cancelled(self):
        return self._ev.is_set()

class Reporter:
    """Feedback channel for the core. This base is silent (suitable for use as a library);
    the CLI uses ConsoleReporter and the GUI will have its own."""

    def __init__(self, cancel=None):
        self.cancel = cancel or CancelToken()

    def log(self, *a): pass
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

class Heartbeat:
    """For long calls with no internal progress (import/export): logs elapsed time every few
    seconds, plus the text from status() when given (e.g. Anki's own progress)."""
    def __init__(self, rep, msg, every=3, status=None):
        self.rep, self.msg, self.every, self.status = rep, msg, every, status

    def __enter__(self):
        self.t0, self.stop = time.time(), threading.Event()
        self.rep.log(self.msg + "...")
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        last = None
        while not self.stop.wait(self.every):
            try:
                info = self.status() if self.status else ""
            except Exception:
                info = ""
            if info and info == last:
                continue                                # nothing new to show
            last = info
            self.rep.log(f"  {self.msg}... {int(time.time() - self.t0)}s" + (f" - {info}" if info else ""))

    def __exit__(self, exc_type, *rest):
        self.stop.set()
        if exc_type is None:
            self.rep.log(f"{self.msg}: finished in {time.time() - self.t0:.1f}s")
        return False