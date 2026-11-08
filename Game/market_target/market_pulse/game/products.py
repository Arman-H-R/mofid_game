"""Which product, and at which size, every new box shows. Pure Python, no pygame.

Products come from a shuffled bag: every one of the 13 is drawn once before any is drawn
again, so the wall keeps showing different products instead of the same few. A product that
is already on the wall, or the one that was just hit, is skipped (and stays in the bag for
the next box). With every other product up, the one just hit comes back; only with more boxes
than products (Market Rush extras on a full wall) may one appear twice.

Sizes come from a second bag, filled in proportion to targets.<size>.weight and shuffled,
so the mix stays balanced without becoming a pattern. targets.min_tiny keeps at least that
many tiny boxes on a wall of three or more, because a tiny hit is the way into Market Rush.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from ..config import TIERS, Section


class Picker:
    def __init__(self, cfg: Section, rng: np.random.Generator):
        self.cfg = cfg
        self.rng = rng
        self._bag: list[str] = []
        self._tier_bag: list[str] = []

    # -- products ---------------------------------------------------------------------------
    @property
    def keys(self) -> list[str]:
        return [p["key"] for p in self.cfg.raw()["products"]]

    def _refill(self) -> None:
        keys = self.keys
        self._bag = [keys[i] for i in self.rng.permutation(len(keys))]

    def product(self, on_wall: Iterable[str] = (), avoid: Iterable[str] = ()) -> str:
        """The next product: not on the wall, not in avoid, if there is any such product."""
        on_wall, avoid = set(on_wall), set(avoid)
        keys = self.keys
        free = [k for k in keys if k not in on_wall and k not in avoid]
        if not free:  # every other product is up: the one just hit comes back rather than a twin
            free = [k for k in keys if k not in on_wall]
        if not free:  # more boxes than products (Market Rush extras): a second copy, not the one just hit
            free = [k for k in keys if k not in avoid] or keys
            return free[int(self.rng.integers(len(free)))]
        for _ in range(2):  # the bag may hold only products that are up; then a fresh bag
            for i, key in enumerate(self._bag):
                if key in free:
                    return self._bag.pop(i)
            self._refill()
        return free[int(self.rng.integers(len(free)))]

    # -- sizes ------------------------------------------------------------------------------
    def _refill_tiers(self) -> None:
        t = self.cfg.targets
        weights = {tier: max(0, int(round(float(t[tier].weight)))) for tier in TIERS}
        bag = [tier for tier, n in weights.items() for _ in range(n)] or list(TIERS)
        self._tier_bag = [bag[i] for i in self.rng.permutation(len(bag))]

    def tier(self, others: Iterable[str] = ()) -> str:
        """The size of the next box. ``others``: the sizes of the boxes that stay on the wall."""
        others = list(others)
        t = self.cfg.targets
        need = int(t.min_tiny)
        wall = len(others) + 1
        if wall >= 3 and others.count("tiny") < min(need, wall):
            return "tiny"
        if not self._tier_bag:
            self._refill_tiers()
        return self._tier_bag.pop()
