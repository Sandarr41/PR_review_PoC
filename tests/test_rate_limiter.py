import threading
import time

import pytest

from pr_review_agent.rate_limiter import RateLimiter


def test_acquire_does_not_block_under_the_limit():
    limiter = RateLimiter(max_calls=3, period_seconds=60.0)
    waited = [limiter.acquire() for _ in range(3)]
    assert all(w == 0.0 for w in waited)


def test_acquire_blocks_once_the_limit_is_reached():
    limiter = RateLimiter(max_calls=2, period_seconds=0.3)
    limiter.acquire()
    limiter.acquire()

    start = time.monotonic()
    waited = limiter.acquire()
    elapsed = time.monotonic() - start

    assert waited > 0
    assert elapsed >= 0.2  # allow scheduler jitter below the 0.3s window


def test_rate_limiter_is_thread_safe_and_enforces_the_window():
    limiter = RateLimiter(max_calls=5, period_seconds=0.5)
    call_times: list[float] = []
    lock = threading.Lock()

    def worker():
        limiter.acquire()
        with lock:
            call_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(10)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 10 calls at 5/0.5s must take at least ~0.5s (the second batch of 5
    # has to wait for the first window to roll over) — this is the whole
    # point of the limiter, so a too-fast completion means it didn't throttle.
    assert time.monotonic() - start >= 0.4
    assert len(call_times) == 10


def test_rejects_non_positive_max_calls():
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0)
