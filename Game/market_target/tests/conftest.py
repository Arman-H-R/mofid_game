import copy
import os
import sys
from pathlib import Path

# Headless SDL for every test that touches pygame.
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # as main.py does

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from market_pulse import config  # noqa: E402


# The original scenario, which stays available through settings: 10 balls, the how-to
# page, no name screen, no logo intro, no Market Rush. The tests of those rules run in this mode.
CLASSIC = {"round": {"ball_limit": True, "balls": 10}, "flow": {"registration": False, "show_instructions": True, "intro_s": 0},
           "rush": {"enabled": False}, "combo": {"enabled": True},
           "targets": {"points_by_size": False}, "layout": {"move_on_hit": False}}

# The rules before MOFID_UI v11 (still there, behind settings): the multiplier, Market Rush,
# every product its own size and points, and the other boxes staying put after a hit.
V4 = {"combo": {"enabled": True}, "rush": {"enabled": True}, "targets": {"points_by_size": False},
      "layout": {"move_on_hit": False}}


def v4_cfg(overrides: dict | None = None) -> config.Section:
    """The game with the rules before MOFID_UI v11, plus ``overrides``."""
    raw = copy.deepcopy(config.DEFAULTS)
    warnings: list[str] = []
    config._merge(raw, copy.deepcopy(START_BOX), (), warnings)
    config._merge(raw, copy.deepcopy(V4), (), warnings)
    if overrides:
        config._merge(raw, overrides, (), warnings)
    config._validate(raw, warnings)
    assert not warnings, warnings
    return config.Section(raw)


# The start box and the name screen (still there, behind settings; the game ships without them:
# it starts from the phone page or Space / Enter at once). Their tests run with them on.
START_BOX = {"flow": {"start_by_throw": True, "quick_start": False}}


def game_cfg(overrides: dict | None = None, shipped: bool = False) -> config.Section:
    """The game as it ships (with the start box and name screen on, unless ``shipped``), plus
    ``overrides``; fails on any config warning."""
    raw = copy.deepcopy(config.DEFAULTS)
    warnings: list[str] = []
    if not shipped:
        config._merge(raw, copy.deepcopy(START_BOX), (), warnings)
    if overrides:
        config._merge(raw, overrides, (), warnings)
    config._validate(raw, warnings)
    assert not warnings, warnings
    return config.Section(raw)


def make_cfg(overrides: dict | None = None) -> config.Section:
    """The original scenario's rules plus ``overrides``."""
    raw = copy.deepcopy(CLASSIC)
    for section, values in (overrides or {}).items():
        raw[section] = {**raw.get(section, {}), **values} if isinstance(values, dict) else values
    return game_cfg(raw)


@pytest.fixture
def cfg():
    return make_cfg()


@pytest.fixture
def rng():
    return np.random.default_rng(1234)
