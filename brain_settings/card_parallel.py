"""A rolling worker window; its owner retains the writer lease until every task settles."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_card_window(records, concurrency, process, can_start, settled, in_flight):
    remaining = iter(records)
    active = set()
    exhausted = False
    failure = None
    try:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="brain-card") as pool:
            while True:
                while len(active) < concurrency and not exhausted and failure is None:
                    try:
                        if not can_start():
                            exhausted = True
                            break
                        record = next(remaining)
                        active.add(pool.submit(process, record))
                        in_flight(len(active))
                    except StopIteration:
                        exhausted = True
                    except Exception as error:
                        failure = error
                if not active:
                    break
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    active.remove(future)
                    result, error = None, None
                    try:
                        result = future.result()
                    except Exception as caught:
                        error = caught
                        failure = failure or caught
                    try:
                        settled(result, error)
                        in_flight(len(active))
                    except Exception as caught:
                        failure = failure or caught
        if failure is not None:
            raise failure
    finally:
        in_flight(0)
