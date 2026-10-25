from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import statistics
import time

from ankitool.core.progress import Reporter


@dataclass
class BenchRow:
    workers: int
    seconds: float
    failed: int                                  # calls that gave up after all retries
    errors: int                                  # failed attempts (rate limit, server error, bad reply)
    avg_latency: float
    max_latency: float
    texts_per_s: float
    sample_error: str = ""

    @property
    def clean(self):
        return self.errors == 0 and self.failed == 0

class ProviderBenchmark:
    """Measures a provider's speed with 1, 2, 4... parallel calls and where it starts failing.
    Makes REAL calls (cost/quota). Stops at the first worker count that produces errors."""

    def __init__(self, client, calls=8, batch=10, workers=(1, 2, 4, 8), rep=None):
        self.client, self.calls, self.batch = client, calls, batch
        self.workers, self.rep = sorted(set(workers)), rep or Reporter()
        client.on_wait = self.rep.waiting
        client.cancel_event = self.rep.cancel.event

    def run(self, target, source="EN"):
        texts = [f"Hello world, this is test sentence number {i}." for i in range(self.batch)]
        lat, err = [], []
        original = self.client._translate

        def timed(*args, **kw):                  # records latency and every failed attempt
            t = time.perf_counter()
            try:
                return original(*args, **kw)
            except Exception as e:
                err.append(str(e)[:80])
                raise
            finally:
                lat.append(time.perf_counter() - t)

        rows = []
        self.client._translate = timed
        try:
            for w in self.workers:
                self.rep.check_cancelled()
                lat.clear(); err.clear()
                self.rep.log(f"testing {w} worker(s): {self.calls} calls x {self.batch} texts...")
                t0 = time.perf_counter()
                with ThreadPoolExecutor(w) as pool:
                    res = list(pool.map(lambda _: self.client.translate(texts, target, source),
                                        range(self.calls)))
                secs = time.perf_counter() - t0
                rows.append(BenchRow(
                    w, secs, sum(r is None for r in res), len(err),
                    statistics.mean(lat) if lat else 0.0, max(lat, default=0.0),
                    self.calls * self.batch / secs if secs else 0.0, err[0] if err else ""))
                if not rows[-1].clean:
                    break                        # higher counts would only hit the limit harder
        finally:
            del self.client._translate           # back to the class method
        return rows

    @staticmethod
    def recommend(rows):
        """Fewest workers within 10% of the best throughput among the error-free runs (else 1)."""
        clean = [r for r in rows if r.clean]
        if not clean:
            return 1
        best = max(r.texts_per_s for r in clean)
        return min(r.workers for r in clean if r.texts_per_s >= 0.9 * best)
