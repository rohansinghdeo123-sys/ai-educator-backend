import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import config
from app.rate_limit import SlidingWindowLimiter, client_rate_key


def _req(headers=None, host="203.0.113.9", path="/coach/chat"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=host),
        url=SimpleNamespace(path=path),
    )


class ClientRateKeyTests(unittest.TestCase):
    def test_auth_header_wins_over_ip(self):
        key = client_rate_key(_req({"authorization": "Bearer abc"}))
        self.assertTrue(key.startswith("auth:"))

    def test_no_xff_uses_socket_ip(self):
        self.assertEqual(client_rate_key(_req()), "ip:203.0.113.9:/coach/chat")

    def test_spoofed_xff_first_hop_is_ignored(self):
        # Client-supplied spoof entries come first; trusted proxy appends real IP last.
        req = _req({"x-forwarded-for": "6.6.6.6, 198.51.100.7"})
        with patch.object(config, "TRUST_PROXY_HEADERS", True):
            self.assertEqual(client_rate_key(req), "ip:198.51.100.7:/coach/chat")

    def test_trust_disabled_ignores_xff_entirely(self):
        req = _req({"x-forwarded-for": "6.6.6.6"})
        with patch.object(config, "TRUST_PROXY_HEADERS", False):
            self.assertEqual(client_rate_key(req), "ip:203.0.113.9:/coach/chat")


class SlidingWindowLimiterTests(unittest.TestCase):
    def test_limit_enforced_with_retry_after(self):
        limiter = SlidingWindowLimiter()
        for _ in range(3):
            allowed, _ = limiter.allow("k", 3, 60)
            self.assertTrue(allowed)
        allowed, retry_after = limiter.allow("k", 3, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

    def test_stale_keys_evicted_when_over_cap(self):
        limiter = SlidingWindowLimiter()
        limiter.MAX_TRACKED_KEYS = 5
        import time as _t

        old = _t.time() - 120
        for i in range(10):  # stale buckets outside the window
            limiter._events[f"stale{i}"].append(old)
        limiter.allow("fresh", 10, 60)
        self.assertLessEqual(len(limiter._events), 5 + 1)


if __name__ == "__main__":
    unittest.main()
