#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Hard spend cap for one evaluation run."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Budget:
    """Tracks real spend (OpenRouter ``usage.cost``) and refuses new calls past ``stop_at``.

    ``stop_at`` sits below ``limit`` so calls already in flight (at most the runner's
    concurrency) cannot carry the run over ``limit``.
    """

    limit: float
    stop_at: float
    spent: float = 0.0
    calls: int = 0
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

    def can_afford(self, estimate: float) -> bool:
        return not self.exhausted and estimate <= self.remaining
