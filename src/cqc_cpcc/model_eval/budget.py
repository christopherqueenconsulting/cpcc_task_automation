#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Hard spend cap for one evaluation run."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field


@dataclass
class Budget:
    """Tracks real spend (OpenRouter ``usage.cost``) and refuses new calls past ``stop_at``.

    ``stop_at`` sits below ``limit`` so calls already in flight (at most the runner's
    concurrency) cannot carry the run over ``limit``.

    A fixed gap only covers a fixed number of calls in flight. The adaptive A/B runs up to 64 per
    side, so each call also **reserves** its worst-case cost (:meth:`reserve`) before it is sent and
    settles it when it finishes (:meth:`settle`). A call starts only while ``spent + reserved +
    its estimate`` fits under ``limit``: the margin below ``limit`` is therefore always (calls in
    flight x their estimate), whatever the concurrency. A call that does not fit waits for the
    in-flight ones to settle; it is refused only when nothing in flight could make room.
    """

    limit: float
    stop_at: float
    spent: float = 0.0
    calls: int = 0
    reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if not 0 < self.stop_at <= self.limit:
            raise ValueError("need 0 < stop_at <= limit")

    @property
    def remaining(self) -> float:
        return max(0.0, self.stop_at - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.stop_at

    def add(self, cost: float) -> None:
        with self._lock:
            self.spent += max(0.0, cost or 0.0)
            self.calls += 1

    def reserve(self, estimate: float) -> str:
        """Set aside ``estimate`` USD for one call about to be sent.

        ``"ok"``: reserved, send it. ``"wait"``: it would not fit beside the calls already in flight,
        try again once some settle. ``"exhausted"``: the budget is spent (``stop_at``), or the call
        would pass ``limit`` even with nothing else in flight.
        """
        try:
            estimate = float(estimate)
        except (TypeError, ValueError):
            return "exhausted"
        if not math.isfinite(estimate) or estimate <= 0:
            return "exhausted"  # no usable price: a call that cannot be bounded must not run for free
        with self._lock:
            if self.spent >= self.stop_at:
                return "exhausted"
            if self.spent + self.reserved + estimate <= self.limit + 1e-12:
                self.reserved += estimate
                return "ok"
            return "wait" if self.reserved > 0 else "exhausted"

    def unreserve(self, amount: float) -> None:
        """Give back a reservation without spending (a call that was never sent, or cost nothing)."""
        with self._lock:
            self.reserved = max(0.0, self.reserved - max(0.0, amount or 0.0))

    def settle(self, reserved: float, cost: float) -> None:
        """Replace a call's reservation with what it really cost, in one step (no gap to slip through)."""
        with self._lock:
            self.reserved = max(0.0, self.reserved - max(0.0, reserved or 0.0))
            self.spent += max(0.0, cost or 0.0)
            self.calls += 1

    def can_afford(self, estimate: float) -> bool:
        return not self.exhausted and estimate <= self.remaining

    def split(self, weights: tuple = (1, 1)) -> list["Budget"]:
        """Carve what is left into independent budgets, one per concurrent run.

        Each child gets its weighted share of both the remaining hard limit and the remaining
        ``stop_at``, so the children's ``stop_at`` values add up to this budget's, and neither run
        can spend the other's share. Hand the children back with :meth:`absorb` when they finish.
        In-flight calls can still carry a child past its ``stop_at``: by at most (calls in flight
        on that child) x (the dearest single call), which the gap between ``stop_at`` and
        ``limit`` has to cover for every child at once.
        """
        total = float(sum(weights))
        left_limit = max(self.limit - self.spent - self.reserved, 1e-9)
        left_stop = max(self.remaining, 1e-9)
        children = [Budget(limit=left_limit * w / total, stop_at=min(left_stop * w / total, left_limit * w / total))
                    for w in weights]
        if self.exhausted:  # nothing left to share: the children start spent, so they refuse every call
            for child in children:
                child.spent = child.stop_at
        return children

    def absorb(self, child: "Budget") -> None:
        """Charge a finished child's spend and calls to this budget."""
        with self._lock:
            self.spent += child.spent
            self.calls += child.calls
