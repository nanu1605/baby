"""Client-side token bucket for NIM calls (NIM_MIGRATION_PLAN.md §2.3).

One bucket is shared across ALL NIM traffic — interactive turns, health
probes, background tasks and the N1 bench — capped safely under the free
tier's ~40 RPM baseline (default 36). Background callers may hold at most
`background_share` of the window so interactive turns keep headroom. The
live router NEVER waits on the bucket (empty → route local, no queueing);
`acquire_wait` exists for batch callers like the bench, which have nowhere
better to go.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class TokenBucket:
    """Sliding 60 s window request limiter with a background-share cap."""

    def __init__(self, rpm: int = 36, background_share: float = 0.5) -> None:
        self.rpm = int(rpm)
        self.background_share = float(background_share)
        self._stamps: deque[tuple[float, bool]] = deque()  # (monotonic, background)

    def _prune(self, now: float) -> None:
        while self._stamps and now - self._stamps[0][0] >= 60.0:
            self._stamps.popleft()

    def try_acquire(self, *, background: bool = False) -> bool:
        """Take one slot now; False when the window (or background share) is full."""
        now = time.monotonic()
        self._prune(now)
        if len(self._stamps) >= self.rpm:
            return False
        if background:
            used = sum(1 for _, bg in self._stamps if bg)
            if used >= int(self.rpm * self.background_share):
                return False
        self._stamps.append((now, background))
        return True

    def seconds_until_slot(self) -> float:
        """Time until the oldest stamp leaves the window (0 when a slot is free)."""
        now = time.monotonic()
        self._prune(now)
        if len(self._stamps) < self.rpm:
            return 0.0
        return max(0.0, 60.0 - (now - self._stamps[0][0]))

    async def acquire_wait(self, *, background: bool = False) -> None:
        """Sleep until a slot frees up, then take it (batch callers only)."""
        while not self.try_acquire(background=background):
            await asyncio.sleep(max(0.25, self.seconds_until_slot()))
