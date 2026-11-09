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
    assert cfg.targets.tiny.points > cfg.targets.small.points > cfg.targets.medium.points > 0
    assert cfg.targets.active == 4 and len(cfg.raw()["products"]) == 9


# the score table: 10 points (big box), 15 (medium box), 30 (small box, starts Market Rush)
TABLE = {"ایزی تریدر": ("medium", 10), "مفید اپ": ("medium", 10), "بورس ویو ۲.۰": ("medium", 10),
         "بیمه زندگی مفید": ("small", 15), "آموزش مفید": ("small", 15), "جت کارت": ("small", 15),
         "آتیه": ("tiny", 30), "عیار": ("tiny", 30), "جت پی": ("tiny", 30)}


def test_the_9_product_tiles_ship_with_the_game_with_their_size_and_points():
    cfg, _ = config.load(None)
    raw = cfg.raw()
    keys = [p["key"] for p in raw["products"]]
    assert len(set(keys)) == 9
    for p in raw["products"]:
        assert (ROOT / "assets" / "products" / p["file"]).is_file(), p["file"]
        assert TABLE[p["name"]] == (p["tier"], p["points"])
    assert {p["name"] for p in raw["products"]} == set(TABLE)
    for gone in ("gold", "hamnag", "tavan", "hami", "boursview2", "jet"):  # taken out, tile files too
        assert gone not in keys and not (ROOT / "assets" / "products" / f"{gone}.png").exists()
    assert config.tier_points(raw, "tiny") == 30 and config.products_of(raw, "small") == ["bimeh", "amoozesh", "jetcard"]


def test_an_older_product_list_becomes_the_9_products(tmp_path):
    """A settings.json from before the products had their own size and points."""
    old = [{"key": k, "name": k, "file": f"{k}.png", "aspect": 0.92} for k in ("atiyeh", "hami", "jet")]
    cfg, warnings = load_json(tmp_path, {"products": old})
    assert [p["key"] for p in cfg.raw()["products"]] == [p["key"] for p in config.PRODUCTS]
    assert any("older list" in w for w in warnings)


def test_products_taken_out_stay_out_of_a_settings_file(tmp_path):
    new = [dict(p) for p in config.PRODUCTS[:2]] + [{"key": "hami", "name": "حامی", "file": "hami.png", "tier": "small"}]
    cfg, warnings = load_json(tmp_path, {"products": new})
    assert [p["key"] for p in cfg.raw()["products"]] == ["easytrader", "mofidapp"]
    assert sum("no longer in the game" in w for w in warnings) == 1


def test_a_product_points_and_size_are_checked(tmp_path):
    new = [{"key": "a", "name": "a", "file": "a.png", "tier": "huge", "points": -3},
           {"key": "b", "name": "b", "file": "b.png", "tier": "tiny"}]
    cfg, warnings = load_json(tmp_path, {"products": new})
    a, b = cfg.raw()["products"]
    assert (a["tier"], a["points"]) == ("medium", 10) and (b["tier"], b["points"]) == ("tiny", 30)
    assert len(warnings) == 2


def test_bad_products_fall_back_to_the_9(tmp_path):
    cfg, warnings = load_json(tmp_path, {"products": [{"key": "x", "tier": "tiny"}, "y"]})
    assert warnings and len(cfg.raw()["products"]) == 9


def test_active_targets_are_kept_within_20(tmp_path):
    cfg, warnings = load_json(tmp_path, {"targets": {"active": 40}})
    assert warnings and cfg.targets.active == 20
    cfg, warnings = load_json(tmp_path, {"targets": {"active": 0}})
    assert warnings and cfg.targets.active == 1
    cfg, warnings = load_json(tmp_path, {"targets": {"active": 7}})
    assert warnings == [] and cfg.targets.active == 7


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
        ({"rush": {"spawn_tiers": ["purple"]}}, ("rush", "spawn_tiers"), ["tiny", "small", "medium", "small"]),
        ({"profiles": {"score_tiers": [700, 250]}}, ("profiles", "score_tiers"), config.DEFAULTS["profiles"]["score_tiers"]),
        ({"display": {"fps": 1000}}, ("display", "fps"), 240),
        ({"display": {"fullscreen": 1}}, ("display", "fullscreen"), True),
        ({"display": {"resolution": [1920]}}, ("display", "resolution"), [0, 0]),
        ({"targets": {"medium": {"points": -5}}}, ("targets", "medium", "points"), 0),
        ({"motion": {"tiny": {"pattern": "zigzag"}}}, ("motion", "tiny", "pattern"), "still"),
        ({"input": {"udp_json": {"units": "inch"}}}, ("input", "udp_json", "units"), "normalized"),
        ({"targets": {"green": {"points": 5}}}, ("targets", "active"), 4),  # the old colours: reported, ignored
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


def test_a_round_always_has_a_time_or_a_ball_limit(tmp_path):
    cfg, warnings = load_json(tmp_path, {})
    assert cfg.round.time_limit and not cfg.round.ball_limit and warnings == []  # as it ships: time only
    cfg, warnings = load_json(tmp_path, {"round": {"time_limit": False}})
    assert cfg.round.time_limit and any("never end" in w for w in warnings)
    cfg, warnings = load_json(tmp_path, {"round": {"time_limit": False, "ball_limit": True, "balls": 8}})
    assert not cfg.round.time_limit and warnings == []
