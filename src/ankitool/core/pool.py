from concurrent.futures import ThreadPoolExecutor, as_completed


def run_chunks(fn, chunks, workers, rep, on_result):
    """Runs fn(chunk) for every chunk in `workers` threads. on_result(chunk, result) is called
    on the CALLING thread as each one finishes, so cache and reporter are never touched by workers.
    On error or cancel, pending chunks are dropped and running ones are waited for."""
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(fn, c): c for c in chunks}
        for fut in as_completed(futures):
            rep.check_cancelled()
            on_result(futures[fut], fut.result())
    except KeyboardInterrupt:
        rep.cancel.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)