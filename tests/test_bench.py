from ankitool.ops.bench import ProviderBenchmark


class Stub:
    """Minimal client: succeeds, or raises (-> translate gives None) from call number `fail_from`."""
    label = "stub"

    def __init__(self, fail_from=None):
        self.n, self.fail_from = 0, fail_from

    def _translate(self, texts, target, source, html):
        self.n += 1
        if self.fail_from is not None and self.n >= self.fail_from:
            raise ConnectionError("429")
        return list(texts)

    def translate(self, texts, target, source=None, protect_tags=False):
        try:
            return self._translate(texts, target, source, protect_tags)
        except ConnectionError:
            return None


def test_clean_run_tests_every_worker_count():
    rows = ProviderBenchmark(Stub(), calls=4, batch=3).run("PT-BR")
    assert [r.workers for r in rows] == [1, 2, 4, 8]
    assert all(r.clean for r in rows)
    assert ProviderBenchmark.recommend(rows) in (1, 2, 4, 8)


def test_stops_at_first_errors_and_recommends_one():
    stub = Stub(fail_from=1)
    rows = ProviderBenchmark(stub, calls=4, batch=3).run("PT-BR")
    assert len(rows) == 1 and rows[0].errors == 4 and rows[0].failed == 4
    assert rows[0].sample_error == "429"
    assert ProviderBenchmark.recommend(rows) == 1
    assert "_translate" not in vars(stub)                 # instrumentation removed
