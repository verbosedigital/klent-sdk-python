import threading
import time

from klent_sdk.buffer import EventBuffer


def test_flushes_when_batch_size_hit():
    calls: list[list[dict]] = []

    def flush(batch):
        calls.append(list(batch))

    buf = EventBuffer(flush_fn=flush, max_batch_size=3, flush_interval_seconds=10)
    buf.enqueue({"i": 1})
    buf.enqueue({"i": 2})
    assert calls == []
    buf.enqueue({"i": 3})
    assert len(calls) == 1
    assert [e["i"] for e in calls[0]] == [1, 2, 3]
    assert buf.size() == 0


def test_flushes_on_timer():
    calls: list[list[dict]] = []

    def flush(batch):
        calls.append(list(batch))

    buf = EventBuffer(flush_fn=flush, max_batch_size=100, flush_interval_seconds=0.05)
    buf.enqueue({"i": 1})
    buf.enqueue({"i": 2})

    deadline = time.monotonic() + 2.0
    while buf.size() > 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert buf.size() == 0
    assert len(calls) == 1
    assert [e["i"] for e in calls[0]] == [1, 2]


def test_flush_noop_when_empty():
    calls: list[list[dict]] = []
    buf = EventBuffer(flush_fn=lambda b: calls.append(b), max_batch_size=10)
    buf.flush()
    assert calls == []


def test_reenqueues_on_flush_failure():
    attempts = {"count": 0}

    def flaky(batch):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")

    buf = EventBuffer(flush_fn=flaky, max_batch_size=10, flush_interval_seconds=10)
    buf.enqueue({"i": 1})
    buf.enqueue({"i": 2})

    try:
        buf.flush()
    except RuntimeError:
        pass

    # Events should still be buffered after failure.
    assert buf.size() == 2
    buf.flush()
    assert buf.size() == 0
    assert attempts["count"] == 2


def test_thread_safety_under_concurrent_enqueue():
    flushed: list[dict] = []
    lock = threading.Lock()

    def flush(batch):
        with lock:
            flushed.extend(batch)

    buf = EventBuffer(flush_fn=flush, max_batch_size=5, flush_interval_seconds=10)

    def producer(start):
        for i in range(start, start + 20):
            buf.enqueue({"i": i})

    threads = [threading.Thread(target=producer, args=(s,)) for s in (0, 100, 200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    buf.flush()
    assert len(flushed) == 60
    assert len({e["i"] for e in flushed}) == 60
