"""A virtual-time asyncio event loop for timing tests (test helper, no product code).

``asyncio.sleep``, ``asyncio.timeout`` and ``asyncio.wait_for`` all schedule against
``loop.time()``. This loop keeps its own clock and, when it would block waiting for the
next timer, jumps the clock forward instead of sleeping. A test can then use the real
timeout formulas with no wall-clock margin to lose on a loaded CI runner: the order of
events is decided by the virtual clock alone, so it cannot flake on scheduler jitter.

Usage::

    loop = VirtualClockLoop()
    try:
        result = loop.run_until_complete(coro())
        elapsed = loop.time() - start   # virtual seconds
    finally:
        loop.close()
"""
import asyncio
import selectors


class _JumpingSelector:
    """Wraps the real selector. A blocking wait advances the virtual clock instead."""

    def __init__(self, real, loop):
        self._real = real
        self._loop = loop

    def select(self, timeout=None):
        if timeout is not None and timeout > 0:
            self._loop._virtual_now += timeout
            timeout = 0
        return self._real.select(timeout)

    def __getattr__(self, name):
        return getattr(self._real, name)


class VirtualClockLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        self._virtual_now = 0.0
        super().__init__(selectors.DefaultSelector())
        self._selector = _JumpingSelector(self._selector, self)

    def time(self):
        return self._virtual_now
