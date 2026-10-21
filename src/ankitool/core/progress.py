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
    """Canal de feedback do núcleo. Esta base é silenciosa (ideal para uso como biblioteca);
    a CLI usa ConsoleReporter e a GUI terá o seu."""

    def __init__(self, cancel=None):
        self.cancel = cancel or CancelToken()

    def log(self, *a): pass
    def warn(self, *a): pass
    def advance(self, label, done, total): pass

    def check_cancelled(self):
        if self.cancel.cancelled:
            raise Cancelled("Cancelado.")

def track(rep, items, label):
    """Percorre 'items' avisando o Reporter do andamento (e respeitando o cancelamento)."""
    total = len(items)
    for i, x in enumerate(items, 1):
        rep.check_cancelled()
        yield x
        rep.advance(label, i, total)

class Heartbeat:
    """Para chamadas longas sem progresso interno (importar/exportar):
    mostra 'ainda trabalhando... Ns' a cada poucos segundos."""
    def __init__(self, rep, msg, every=5):
        self.rep, self.msg, self.every = rep, msg, every

    def __enter__(self):
        self.t0, self.stop = time.time(), threading.Event()
        self.rep.log(self.msg + "...")
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while not self.stop.wait(self.every):
            self.rep.log(f"  {self.msg}... {int(time.time() - self.t0)}s")

    def __exit__(self, exc_type, *rest):
        self.stop.set()
        if exc_type is None:
            self.rep.log(f"{self.msg}: concluído em {time.time() - self.t0:.1f}s")
        return False
