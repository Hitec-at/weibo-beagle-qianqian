from __future__ import annotations

import unittest
from unittest.mock import patch

from fetch_weibo import RateLimiter, StageBudgetExhausted


class StageBudgetTest(unittest.TestCase):
    def test_stops_before_exceeding_run_budget(self) -> None:
        with (
            patch("fetch_weibo.store.reserve_weibo_request", return_value=1) as reserve,
            patch("fetch_weibo.time.sleep"),
        ):
            limiter = RateLimiter(max_requests=1)
            limiter.wait("https://weibo.com/ajax/statuses/mymblog")
            with self.assertRaises(StageBudgetExhausted):
                limiter.wait("https://weibo.com/ajax/statuses/mymblog")

        reserve.assert_called_once()

    def test_rejects_non_positive_run_budget(self) -> None:
        with self.assertRaises(ValueError):
            RateLimiter(max_requests=0)


if __name__ == "__main__":
    unittest.main()
