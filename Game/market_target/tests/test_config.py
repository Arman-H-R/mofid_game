"""Settings loading: a broken settings file must never stop the installation."""
import json

import pytest

from market_pulse import config
from conftest import ROOT


def load_json(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return config.load(p)


def test_defaults_are_valid():
    cfg, warnings = config.load(None)
    assert warnings == []
    assert cfg.round.balls == 10 and cfg.round.duration_s == 60  # whichever comes first
    assert cfg.targets.red.points > cfg.targets.blue.points > cfg.targets.green.points > 0


def test_shipped_settings_file_loads_cleanly():
    path = ROOT / "config" / "settings.json"
    assert path.is_file()
    _, warnings = config.load(path)
    assert warnings == []


def test_missing_file_falls_back(tmp_path):
    cfg, warnings = config.load(tmp_path / "nope.json")
    assert cfg.round.duration_s == 60 and any("not found" in w for w in warnings)


def test_broken_json_falls_back(tmp_path):
    cfg, warnings = load_json(tmp_path, "{ this is not json")
    assert cfg.round.duration_s == 60 and warnings


def test_partial_override_and_comments(tmp_path):
    cfg, warnings = load_json(tmp_path, {"_comment": "hi", "round": {"balls": 12}})
    assert warnings == []
    assert cfg.round.balls == 12 and cfg.round.duration_s == 60


@pytest.mark.parametrize(
    "data, field, expected",
    [
        ({"round": {"balls": "ten"}}, ("round", "balls"), config.DEFAULTS["round"]["balls"]),
        ({"round": {"balls": -1}}, ("round", "balls"), 0),
        ({"rush": {"duration_s": 0}}, ("rush", "duration_s"), 1),
        ({"rush": {"spawn_colors": ["purple"]}}, ("rush", "spawn_colors"), ["red", "blue", "green", "red"]),
        ({"profiles": {"score_tiers": [700, 250]}}, ("profiles", "score_tiers"), config.DEFAULTS["profiles"]["score_tiers"]),
        ({"display": {"fps": 1000}}, ("display", "fps"), 240),
        ({"display": {"fullscreen": 1}}, ("display", "fullscreen"), True),
        ({"display": {"resolution": [1920]}}, ("display", "resolution"), [0, 0]),
        ({"targets": {"green": {"points": -5}}}, ("targets", "green", "points"), 0),
        ({"motion": {"red": {"pattern": "zigzag"}}}, ("motion", "red", "pattern"), "still"),
        ({"input": {"udp_json": {"units": "inch"}}}, ("input", "udp_json", "units"), "normalized"),
        ({"round": {"initial": ["purple"]}}, ("round", "initial"), ["green", "red", "blue"]),
        ({"round": "x"}, ("round", "balls"), config.DEFAULTS["round"]["balls"]),
    ],
)
def test_bad_values_are_reported_and_replaced(tmp_path, data, field, expected):
    cfg, warnings = load_json(tmp_path, data)
    assert warnings
    value = cfg
    for key in field:
        value = value[key]
    assert value == expected


def test_unknown_keys_are_reported(tmp_path):
    _, warnings = load_json(tmp_path, {"round": {"bals": 3}})
    assert any("round.bals" in w for w in warnings)


def test_combo_levels_are_validated_and_sorted(tmp_path):
    cfg, warnings = load_json(
        tmp_path,
        {"combo": {"levels": [{"after_hits": 5, "multiplier": 3}, {"after_hits": 0, "multiplier": 2},
                              {"after_hits": 2, "multiplier": 1.5}]}},
    )
    assert len(warnings) == 1
    assert cfg.combo.raw()["levels"] == [{"after_hits": 2, "multiplier": 1.5}, {"after_hits": 5, "multiplier": 3}]


def test_settings_are_read_only():
    cfg, _ = config.load(None)
    with pytest.raises(AttributeError):
        cfg.round.balls = 3


def test_write_defaults_round_trip(tmp_path):
    p = tmp_path / "out" / "settings.json"
    config.write_defaults(p)
    cfg, warnings = config.load(p)
    assert warnings == [] and cfg.raw() == config.DEFAULTS
