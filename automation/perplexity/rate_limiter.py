"""
Token-bucket rate limiter for Perplexity API calls.
Configurable max calls per minute window.
"""
import time
import threading


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_calls_per_minute: int = 20):
        self.max_calls = max_calls_per_minute
        self.window = 60.0  # seconds
        self.calls: list[float] = []
        self._lock = threading.Lock()

    def wait_if_needed(self):
        """Block until a call is allowed within the rate limit window."""
        with self._lock:
            now = time.time()
            # Remove calls outside the window
            self.calls = [t for t in self.calls if now - t < self.window]

            if len(self.calls) >= self.max_calls:
                sleep_time = self.window - (now - self.calls[0]) + 0.1
                print(f"  [RATE LIMIT] {len(self.calls)} calls in window, sleeping {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                # Clean up again after sleep
                now = time.time()
                self.calls = [t for t in self.calls if now - t < self.window]

            self.calls.append(time.time())

    @property
    def calls_remaining(self) -> int:
        now = time.time()
        active = [t for t in self.calls if now - t < self.window]
        return max(0, self.max_calls - len(active))


# Global instance — import and use across all modules
limiter = RateLimiter(max_calls_per_minute=20)
