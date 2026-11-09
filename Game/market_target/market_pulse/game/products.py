"""Which product, and at which size, every new box shows. Pure Python, no pygame.

Every product has its own box size (its "tier"): the size is drawn first, then a product of
that size. Products come from a shuffled bag: every one is drawn once before any is drawn
again, so the wall keeps showing different products instead of the same few. A product that
is already on the wall, or the one that was just hit, is skipped (and stays in the bag for
the next box). With every other product up, the one just hit comes back; only with more boxes
than products (Market Rush extras on a full wall) may one appear twice.

Sizes come from a second bag, filled in proportion to targets.<size>.weight and shuffled,
so the mix stays balanced without becoming a pattern. targets.min_tiny keeps at least that
many tiny boxes on a wall of three or more, because a tiny hit is the way into Market Rush.
A size with no product is never drawn.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from ..config import TIERS, Section, products_of


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

    def product(self, on_wall: Iterable[str] = (), avoid: Iterable[str] = (), tier: str | None = None) -> str:
        """The next product (of this size, if it has any): not on the wall, not in avoid, if
        there is any such product."""
        up = list(on_wall)
        on_wall, avoid = set(up), set(avoid)
        keys = (products_of(self.cfg.raw(), tier) if tier else []) or self.keys
        free = [k for k in keys if k not in on_wall and k not in avoid]
        if not free:  # every other product is up: the one just hit comes back rather than a twin
            free = [k for k in keys if k not in on_wall]
        if not free:  # more boxes than products (Market Rush extras, or targets.active above them):
            # another copy of a product that is up the fewest times, not the one just hit
            cands = [k for k in keys if k not in avoid] or keys
            fewest = min(up.count(k) for k in cands)
            free = [k for k in cands if up.count(k) == fewest]
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
        sizes = self.sizes
        weights = {tier: max(0, int(round(float(t[tier].weight)))) for tier in sizes}
        bag = [tier for tier, n in weights.items() for _ in range(n)] or list(sizes)
        self._tier_bag = [bag[i] for i in self.rng.permutation(len(bag))]

    @property
    def sizes(self) -> list[str]:
        """The sizes that have products (all three if none has a size)."""
        raw = self.cfg.raw()
        return [tier for tier in TIERS if products_of(raw, tier)] or list(TIERS)

    def tier(self, others: Iterable[str] = (), on_wall: Iterable[str] = (), avoid: Iterable[str] = ()) -> str:
        """The size of the next box. ``others``: the sizes of the boxes that stay on the wall;
        ``on_wall``: their products; ``avoid``: the product just hit. A size is drawn only
        while it has a product up the fewest times (one not on the wall, while there is any),
        so a size never shows a product twice while another size has one to show."""
        others = list(others)
        t = self.cfg.targets
        need = int(t.min_tiny)
        wall = len(others) + 1
        if wall >= 3 and "tiny" in self.sizes and others.count("tiny") < min(need, wall):
            return "tiny"
        up, avoid = list(on_wall), set(avoid)
        count = {p["key"]: up.count(p["key"]) + (0.5 if p["key"] in avoid else 0.0) for p in self.cfg.raw()["products"]}
        fewest = min(count.values(), default=0.0)
        tier_of = {p["key"]: p.get("tier", "medium") for p in self.cfg.raw()["products"]}
        open_sizes = {tier_of[k] for k, n in count.items() if n == fewest}
        for _ in range(2):  # the bag may hold only sizes with nothing new to show; then a fresh bag
            if not self._tier_bag:
                self._refill_tiers()
            for i, tier in enumerate(self._tier_bag):
                if tier in open_sizes:
                    return self._tier_bag.pop(i)
            self._tier_bag = []
        if not self._tier_bag:  # no size with a weight has one to show: as drawn
            self._refill_tiers()
        return self._tier_bag.pop()
