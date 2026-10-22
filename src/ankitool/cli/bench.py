from ankitool.cli.console import ConsoleReporter, choose_provider
from ankitool.core.errors import ConfigError
from ankitool.ops.bench import ProviderBenchmark


class BenchApp:
    """bench command: times the provider with parallel calls and suggests max_workers.
    Makes real API calls (uses quota)."""

    def __init__(self, opts):
        self.opts = opts

    def run(self):
        o = self.opts
        client = choose_provider(o)
        target = client.resolve_target(o.target) if o.target else client.default_target
        if target is None:
            raise ConfigError(f"Unknown target language: {o.target}. Use the 'langs' command.")
        if not o.quiet:
            print(f"\nBenchmark of {client.label}: real API calls, "
                  f"up to {o.calls * len(o.workers)} (batches of {o.batch} short texts).")
        rows = ProviderBenchmark(client, o.calls, o.batch, o.workers,
                                 ConsoleReporter(quiet=o.quiet)).run(target)
        print(f"\n{'workers':>7} {'total':>8} {'texts/s':>8} {'avg lat':>8} {'max lat':>8} "
              f"{'errors':>6} {'failed':>6}")
        for r in rows:
            print(f"{r.workers:>7} {r.seconds:>7.1f}s {r.texts_per_s:>8.1f} {r.avg_latency:>7.1f}s "
                  f"{r.max_latency:>7.1f}s {r.errors:>6} {r.failed:>6}")
        bad = next((r for r in rows if not r.clean), None)
        if bad:
            print(f"\nErrors from {bad.workers} worker(s) on, e.g.: {bad.sample_error}")
        print(f"\nSuggested max_workers for {client.label}: {ProviderBenchmark.recommend(rows)} "
              f"(a suggestion: depends on your quota and the time of day; not applied automatically)")
