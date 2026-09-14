from __future__ import annotations

import threading
import time
import unittest

from sms_campaign.sheet_mirror import CoalescingMirror


class TestCoalescingMirror(unittest.TestCase):
    def test_burst_of_requests_runs_one_mirror(self) -> None:
        calls: list[int] = []
        finished = threading.Event()

        def mirror() -> None:
            calls.append(1)
            finished.set()

        queue = CoalescingMirror(mirror, delay_seconds=0.01)
        for _ in range(10):
            queue.request()

        self.assertTrue(finished.wait(1))
        time.sleep(0.03)
        self.assertEqual(len(calls), 1)

    def test_event_during_mirror_gets_a_follow_up_mirror(self) -> None:
        calls: list[int] = []
        started = threading.Event()
        finished = threading.Event()

        def mirror() -> None:
            calls.append(1)
            if len(calls) == 1:
                started.set()
                time.sleep(0.03)
            else:
                finished.set()

        queue = CoalescingMirror(mirror, delay_seconds=0.01)
        queue.request()
        self.assertTrue(started.wait(1))
        queue.request()

        self.assertTrue(finished.wait(1))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
