#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The pause gate: the page pauses the browser worker; a keepalive holds the session."""

import threading
import time

import pytest

from cqc_cpcc.pause_gate import PauseGate


class FakeClock:
    """A clock the test moves by hand, so a 60 s keepalive needs no real minute."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _in_thread(function):
    """Run ``function`` on a thread; return (thread, outcome list of results/exceptions)."""
    outcome = []

    def target():
        try:
            outcome.append(function())
        except BaseException as error:  # noqa: BLE001 - the test inspects what the worker saw
            outcome.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _wait_until(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.002)
    return False


@pytest.mark.unit
class TestPauseAndResume:
    def test_running_gate_does_not_block(self):
        gate = PauseGate()
        gate.wait_if_paused()  # returns at once
        assert not gate.paused
        assert gate.paused_since is None

    def test_paused_gate_blocks_until_resumed(self):
        gate = PauseGate(poll_interval=0.001)
        gate.pause()
        assert gate.paused

        thread, outcome = _in_thread(gate.wait_if_paused)
        time.sleep(0.05)
        assert thread.is_alive(), "the worker should be held while paused"

        gate.resume()
        thread.join(2)
        assert not thread.is_alive()
        assert outcome == [None]
        assert not gate.paused
        assert gate.paused_since is None

    def test_pausing_twice_keeps_the_first_time(self):
        times = iter([100.0, 200.0])
        gate = PauseGate(wall_clock=lambda: next(times))
        gate.pause()
        gate.pause()
        assert gate.paused_since == 100.0

    def test_resume_when_not_paused_is_harmless(self):
        gate = PauseGate()
        gate.resume()
        assert not gate.paused


@pytest.mark.unit
class TestCancelWhilePaused:
    def test_cancel_ends_the_wait_with_the_cancel_error(self):
        class Cancelled(BaseException):
            pass

        cancelled = threading.Event()

        def raise_if_cancelled():
            if cancelled.is_set():
                raise Cancelled()

        gate = PauseGate(raise_if_cancelled=raise_if_cancelled, poll_interval=0.001)
        gate.pause()
        thread, outcome = _in_thread(gate.wait_if_paused)
        time.sleep(0.03)
        assert thread.is_alive()

        cancelled.set()  # the gate stays paused: cancel alone must free the worker
        thread.join(2)
        assert not thread.is_alive()
        assert gate.paused
        assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)

    def test_cancel_is_not_checked_while_running(self):
        gate = PauseGate(raise_if_cancelled=lambda: pytest.fail("checked while running"))
        gate.wait_if_paused()


@pytest.mark.unit
class TestKeepalive:
    def _paused_gate(self, clock, keepalive, interval=60.0):
        gate = PauseGate(keepalive=keepalive, keepalive_interval=interval, clock=clock,
                         poll_interval=0.001)
        gate.pause()
        return gate

    def test_keepalive_fires_each_interval_while_paused(self):
        clock = FakeClock()
        calls = []
        gate = self._paused_gate(clock, lambda: calls.append(clock.now))
        thread, _ = _in_thread(gate.wait_if_paused)
        try:
            time.sleep(0.03)
            assert calls == [], "no keepalive before the first interval"

            clock.now += 61
            assert _wait_until(lambda: len(calls) == 1)
            time.sleep(0.03)
            assert len(calls) == 1, "one keepalive per interval, not one per poll"

            clock.now += 60
            assert _wait_until(lambda: len(calls) == 2)
            assert gate.keepalive_count == 2
        finally:
            gate.resume()
            thread.join(2)

    def test_no_keepalive_when_not_paused(self):
        clock = FakeClock()
        calls = []
        gate = PauseGate(keepalive=lambda: calls.append(1), clock=clock, poll_interval=0.001)
        clock.now += 600
        gate.wait_if_paused()
        assert calls == []

    def test_no_keepalive_after_resume(self):
        clock = FakeClock()
        calls = []
        gate = self._paused_gate(clock, lambda: calls.append(1))
        thread, _ = _in_thread(gate.wait_if_paused)
        time.sleep(0.02)
        gate.resume()
        thread.join(2)
        clock.now += 600
        gate.wait_if_paused()
        time.sleep(0.02)
        assert calls == []

    def test_a_failing_keepalive_does_not_end_the_pause(self):
        clock = FakeClock()
        attempts = []

        def broken():
            attempts.append(1)
            raise ConnectionError("browser gone")

        gate = self._paused_gate(clock, broken)
        thread, outcome = _in_thread(gate.wait_if_paused)
        try:
            clock.now += 61
            assert _wait_until(lambda: len(attempts) == 1)
            assert thread.is_alive() and gate.paused
            assert gate.keepalive_count == 0
        finally:
            gate.resume()
            thread.join(2)
        assert outcome == [None]

    def test_keepalive_may_be_assigned_after_creation(self):
        clock = FakeClock()
        calls = []
        gate = self._paused_gate(clock, None)
        gate.keepalive = lambda: calls.append(1)
        thread, _ = _in_thread(gate.wait_if_paused)
        try:
            clock.now += 61
            assert _wait_until(lambda: len(calls) == 1)
        finally:
            gate.resume()
            thread.join(2)
