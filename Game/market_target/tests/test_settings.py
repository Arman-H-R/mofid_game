"""The operator's settings page (F5): its rows, changing values with keys, buttons and the
mouse, saving into the settings file, and the game picking the changes up."""
import json
import time

import pygame
import pytest

from market_pulse import config
from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from market_pulse.ui import settings as S
from conftest import ROOT, game_cfg
from test_app import FakeTime, sensor_hit, step


def all_items(raw=None):
    raw = raw or config.DEFAULTS
    return [it for _, items, _ in S.pages(raw) for it in items]


def make_app(tmp_path, extra=None, settings_file="settings.json"):
    over = {"display": {"resolution": [768, 1024]}, "input": {"sources": []}, "camera": {"enabled": False},
            "flow": {"registration": False, "intro_s": 0, "countdown_s": 1.0},
            "results": {"log_file": str(tmp_path / "results.jsonl")}}
    for k, v in (extra or {}).items():
        over[k] = {**over.get(k, {}), **v}
    clock = FakeTime()
    file = tmp_path / settings_file if settings_file else None
    app = App(game_cfg(over), Paths(ROOT), clock_source=clock, start_inputs=False, settings_file=file)
    return app, clock


def key(app, k, mod=0):
    app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod), app.clock.now())


def mouse(app, kind, pos, **kw):
    app._handle_event(pygame.event.Event(kind, pos=pos, **kw), app.clock.now())


def hit_rect(page, action, arg):
    return next(rect for rect, a, x in page._hits if a == action and x == arg)


def find(page, key_):
    for tab, (_, items, _) in enumerate(page.pages):
        for row, it in enumerate(items):
            if it.key == key_:
                return tab, row
    raise KeyError(key_)


# -- the rows ------------------------------------------------------------------------------------
def test_every_row_is_a_real_setting_with_its_default_inside_the_slider():
    items = all_items()
    assert len({it.key for it in items}) == len(items)
    for it in items:
        d = S.default(it.key)
        if it.switch:
            assert isinstance(d, bool), it.key
        elif it.options:
            assert d in [v for v, _ in it.options], it.key
        else:
            assert isinstance(d, (int, float)) and it.lo <= d <= it.hi, it.key
            assert it.lo < it.hi and it.step > 0


def test_sliders_never_go_outside_what_the_settings_file_allows():
    for it in all_items():
        if len(it.key) == 2 and it.key in config._RANGES:
            lo, hi = config._RANGES[it.key]
            assert lo <= it.lo and it.hi <= hi, it.key


def test_values_snap_to_steps_and_stay_whole_where_the_game_needs_whole_numbers():
    raw = game_cfg().raw()
    points = S.Item(("targets", "tiny", "points"), "", 0, 200, 5, "pts")
    assert S.snap(points, raw, 33.0) == 35 and isinstance(S.snap(points, raw, 33.0), int)
    assert S.snap(points, raw, 999) == 200
    mult = S.Item(("combo", "levels", 0, "multiplier"), "", 1, 10, 0.5, "x")
    assert S.snap(mult, raw, 2.6) == 2.5 and S.snap(mult, raw, 3.1) == 3 and isinstance(S.snap(mult, raw, 3.1), int)
    speed = S.Item(("motion", "speed"), "", 0.2, 3, 0.1, "x")
    assert S.snap(speed, raw, 1.0) == 1.0 and isinstance(S.snap(speed, raw, 1.0), float)
    raw["targets"]["tiny"]["points"] = 12  # from the file, between two steps
    assert S.nudge(points, raw, 1) == 15 and S.nudge(points, raw, -1) == 10


def test_multiplier_levels_stay_in_order():
    raw = game_cfg().raw()  # after 2, 4 and 6 hits in a row
    middle = S.Item(("combo", "levels", 1, "after_hits"), "", 1, 30, 1, "hits")
    assert S.bounds(middle, raw) == (3, 5)
    assert S.snap(middle, raw, 1) == 3 and S.snap(middle, raw, 20) == 5


def test_values_read_well():
    it = lambda **kw: S.Item(("x",), "", **kw)  # noqa: E731
    assert S.shown(it(unit="cm"), 60) == "60 سانتی‌متر"
    assert S.shown(it(unit="x", step=0.5), 2.5) == "×2.5"
    assert S.shown(it(unit="%"), 0.8) == "80 درصد"
    assert S.shown(it(zero="بدون محدودیت"), 0) == "بدون محدودیت"
    assert S.shown(it(switch=True), True) == "روشن"


# -- saving --------------------------------------------------------------------------------------
def test_saving_writes_only_the_changed_values_and_keeps_the_rest_of_the_file(tmp_path):
    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"_comment": "mine", "targets": {"tiny": {"points": 30, "weight": 5}},
                                "web": {"pin": "1234"}}, ensure_ascii=False), encoding="utf-8")
    raw = game_cfg().raw()
    raw["targets"]["tiny"]["points"] = 50
    raw["combo"]["levels"][1]["multiplier"] = 2.5
    raw["display"]["fullscreen"] = False  # as --windowed does: not on the page, never written
    S.write_changes(file, None, raw, [("targets", "tiny", "points"), ("combo", "levels", 1, "multiplier")])
    doc = json.loads(file.read_text(encoding="utf-8"))
    assert doc["_comment"] == "mine" and doc["web"] == {"pin": "1234"}
    assert doc["targets"]["tiny"] == {"points": 50, "weight": 5}
    assert doc["combo"]["levels"][1] == {"after_hits": 4, "multiplier": 2.5}
    assert "display" not in doc
    cfg, warnings = config.load(file)
    assert not warnings and cfg.targets.tiny.points == 50 and cfg.combo.levels[1]["multiplier"] == 2.5
    assert not (tmp_path / "settings.json.tmp").exists()


def test_the_exe_saves_next_to_itself_starting_from_its_built_in_settings(tmp_path):
    inside = tmp_path / "bundle.json"
    inside.write_text(json.dumps({"web": {"title": "x"}}), encoding="utf-8")
    beside = tmp_path / "config" / "settings.json"
    raw = game_cfg().raw()
    raw["round"]["duration_s"] = 90
    S.write_changes(beside, inside, raw, [("round", "duration_s")])
    assert json.loads(beside.read_text(encoding="utf-8")) == {"web": {"title": "x"}, "round": {"duration_s": 90}}


def test_a_broken_settings_file_is_not_overwritten(tmp_path):
    file = tmp_path / "settings.json"
    file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        S.write_changes(file, None, game_cfg().raw(), [("round", "duration_s")])
    assert file.read_text(encoding="utf-8") == "{ not json"


# -- in the game ---------------------------------------------------------------------------------
def test_f5_opens_the_page_and_the_game_stands_still(tmp_path):
    app, clock = make_app(tmp_path)
    try:
        key(app, pygame.K_SPACE)
        step(app, clock, 1.2)
        assert app.flow.state == F.PLAYING
        left = app.flow.round.time_left(app.clock.now())
        key(app, pygame.K_F5)
        assert app.settings.open and app.clock.paused and pygame.mouse.get_visible()
        sensor_hit(app, clock, 150.0, 390.0)
        key(app, pygame.K_SPACE)  # not a start key while the page is open
        step(app, clock, 2.0)
        assert app.flow.round.throws == 0
        assert app.flow.round.time_left(app.clock.now()) == pytest.approx(left)
        app.running = True
        esc_at = app._esc_at
        key(app, pygame.K_ESCAPE)  # closes the page; it does not count towards quitting the game
        assert not app.settings.open and not app.clock.paused
        assert app.running and app._esc_at == esc_at
        assert app.frame_errors == 0
    finally:
        app.shutdown()


def test_f5_opens_the_page_in_a_window_of_its_own(tmp_path, monkeypatch):
    """operator.settings_window: the page has its own window; the game keeps showing in its own."""
    monkeypatch.setattr(App, "_want_settings_window", lambda self: True)
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        key(app, pygame.K_SPACE)
        step(app, clock, 1.2)
        key(app, pygame.K_F5)
        win = app.settings_win
        assert page.open and app.clock.paused and win is not None
        assert page.game_size == app.screen.get_size()
        drawn = []
        real_draw = page.draw
        monkeypatch.setattr(page, "draw", lambda surf, *a: (drawn.append(surf), real_draw(surf, *a)))
        step(app, clock, 0.1)
        assert drawn and all(s is not app.screen for s in drawn)  # into its own window, not over the game
        tab = hit_rect(page, "tab", 1)
        mouse(app, pygame.MOUSEBUTTONDOWN, tab.center, button=1, window=win)
        assert page.tab == 1
        game = pygame.Window.from_display_module()
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "tab", 0).center, button=1, window=game)
        assert page.tab == 1  # a click on the game's window is not a click on the page
        app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5, mod=0, window=game), app.clock.now())
        assert page.open  # F5 on the game only brings the settings window to the front
        app.running = True
        app._handle_event(pygame.event.Event(pygame.WINDOWCLOSE, window=win), app.clock.now())
        assert not page.open and app.settings_win is None and not app.clock.paused and app.running
        assert app.frame_errors == 0
    finally:
        app.shutdown()


def test_a_game_paused_before_stays_paused(tmp_path):
    app, clock = make_app(tmp_path)
    try:
        key(app, pygame.K_SPACE)
        step(app, clock, 1.2)
        key(app, pygame.K_p)
        key(app, pygame.K_F5)
        key(app, pygame.K_F5)
        assert app.clock.paused
    finally:
        app.shutdown()


def test_keys_change_values_live_and_save_writes_them(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        key(app, pygame.K_F5)
        tab, row = find(page, ("targets", "tiny", "points"))
        page.select_tab(tab)
        for _ in range(row):
            key(app, pygame.K_DOWN)
        key(app, pygame.K_RIGHT)
        key(app, pygame.K_RIGHT, pygame.KMOD_SHIFT)  # five steps
        assert app.cfg.targets.tiny.points == 60  # 30 + 5 + 25, in the game at once
        assert page.changed() == [("targets", "tiny", "points")]
        step(app, clock, 0.1)  # the page draws
        key(app, pygame.K_s, pygame.KMOD_CTRL)
        assert not page.open and page.changed() == []
        assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"targets": {"tiny": {"points": 60}}}
    finally:
        app.shutdown()


def test_revert_puts_back_the_saved_values(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        page.select_tab(find(page, ("round", "duration_s"))[0])
        page.row = find(page, ("round", "duration_s"))[1]
        key(app, pygame.K_RIGHT)  # a typed box: the arrows move it a second at a time
        assert app.cfg.round.duration_s == 61
        assert page.press("revert") == "close"
        assert app.cfg.round.duration_s == 60 and page.changed() == []
    finally:
        app.shutdown()


def test_defaults_resets_only_this_page(tmp_path):
    app, clock = make_app(tmp_path, {"round": {"duration_s": 90}, "targets": {"tiny": {"size_cm": 40}}})
    page = app.settings
    try:
        page.select_tab(0)
        page.press("defaults")
        assert app.cfg.targets.tiny.size_cm == 29 and app.cfg.round.duration_s == 90
    finally:
        app.shutdown()


def test_the_mouse_drags_sliders_presses_buttons_and_flips_switches(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        tab, row = find(page, ("targets", "medium", "size_cm"))
        page.select_tab(tab)
        step(app, clock, 0.05)
        track = page._tracks[row]
        mouse(app, pygame.MOUSEBUTTONDOWN, track.midright, button=1)
        assert app.cfg.targets.medium.size_cm == 100
        mouse(app, pygame.MOUSEMOTION, track.midleft, rel=(0, 0), buttons=(1, 0, 0))
        assert app.cfg.targets.medium.size_cm == 15
        mouse(app, pygame.MOUSEBUTTONUP, track.midleft, button=1)
        mouse(app, pygame.MOUSEMOTION, track.midright, rel=(0, 0), buttons=(0, 0, 0))
        assert app.cfg.targets.medium.size_cm == 15  # no longer dragging
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "plus", row).center, button=1)
        assert app.cfg.targets.medium.size_cm == 16

        tab, row = find(page, ("motion", "enabled"))
        page.select_tab(tab)
        step(app, clock, 0.05)
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "row", row).center, button=1)
        assert app.cfg.motion.enabled is False
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "tab", 2).center, button=1)
        assert page.tab == 2
        step(app, clock, 0.05)
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "button", "try").center, button=1)
        assert not page.open and len(page.changed()) == 2
        assert not (tmp_path / "settings.json").exists()  # tried, not saved
    finally:
        app.shutdown()


def test_the_next_round_uses_the_new_values(tmp_path):
    app, clock = make_app(tmp_path, {"rush": {"enabled": False}})
    page = app.settings
    try:
        app.open_settings()
        S.put(page.raw, ("targets", "medium", "size_cm"), 80)
        S.put(page.raw, ("targets", "medium", "points"), 50)
        S.put(page.raw, ("targets", "active"), 7)
        S.put(page.raw, ("round", "balls"), 3)
        S.put(page.raw, ("round", "ball_limit"), True)
        for tier in ("small", "tiny"):  # every box medium, so the hit below is one
            S.put(page.raw, ("targets", tier, "weight"), 0)
        S.put(page.raw, ("targets", "min_tiny"), 0)
        page.on_change()
        app.close_settings()
        key(app, pygame.K_SPACE)
        step(app, clock, 1.2)
        rnd = app.flow.round
        assert len(rnd.targets) == 7 and len({t.product for t in rnd.targets}) == 7
        medium = next(t for t in rnd.targets if t.tier == "medium")
        assert medium.size == 80 and rnd.balls == 3
        x, y = medium.center_at(app.clock.now())
        sensor_hit(app, clock, x, y)
        step(app, clock, 0.2)
        assert rnd.score == 50 and len(rnd.targets) == 7
    finally:
        app.shutdown()


def test_the_number_of_active_boxes_is_on_the_boxes_tab_and_saved(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        tab, row = find(page, ("targets", "active"))
        assert tab == 0 and row == 0  # the first row of the first tab
        item = page.pages[tab][1][row]
        assert (item.lo, item.hi, item.step) == (1, 10, 1)
        assert S.shown(item, 4) == "4 باکس"
        page.select_tab(tab)
        page.row = row
        for _ in range(20):  # it stops at the 10 products
            page.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0))
        assert app.cfg.targets.active == 10
        for _ in range(8):
            page.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT, mod=0))
        assert app.cfg.targets.active == 2
        step(app, clock, 0.05)  # draws the boxes tab with its row of products
        assert page.press("save") == "saved"
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert saved == {"targets": {"active": 2}}
        cfg, warnings = config.load(tmp_path / "settings.json")  # what the game reads when it starts again
        assert warnings == [] and cfg.targets.active == 2
    finally:
        app.shutdown()


def test_shadow_and_volume_follow_at_once(tmp_path):
    app, clock = make_app(tmp_path)
    try:
        S.put(app.cfg.raw(), ("ui", "target_shadow"), False)
        S.put(app.cfg.raw(), ("audio", "volume"), 0.25)
        app.settings.on_change()
        assert app.renderer.shadow is False and app.audio.volume == 0.25
    finally:
        app.shutdown()


def test_without_a_settings_file_it_says_so_and_stays_open(tmp_path):
    app, clock = make_app(tmp_path, settings_file=None)
    try:
        app.open_settings()
        S.put(app.cfg.raw(), ("round", "duration_s"), 90)
        assert app.settings.press("save") is None and app.settings.message[1] == "error"
        assert app.settings.open
    finally:
        app.shutdown()


def test_every_page_draws_quickly_at_wall_size(tmp_path):
    app, clock = make_app(tmp_path, {"display": {"resolution": [1536, 2048]}})
    try:
        app.open_settings()
        worst = []
        for tab in range(len(app.settings.pages)):
            app.settings.select_tab(tab)
            app.draw(app.clock.now())  # first draw renders its texts
            s = time.perf_counter()
            for _ in range(5):
                app.draw(app.clock.now())
            worst.append((time.perf_counter() - s) / 5)
        assert max(worst) < 0.012
    finally:
        app.shutdown()


# -- on its own, from the terminal (main.py --settings) ---------------------------------------------
def post_keys(*keys):
    for k, mod in keys:
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod))


def test_the_page_alone_saves_and_stays_open_then_quits(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    tab, row = find(page, ("round", "duration_s"))
    page.select_tab(tab)
    page.row = row
    pygame.event.clear()
    post_keys((pygame.K_RIGHT, 0), (pygame.K_s, pygame.KMOD_CTRL), (pygame.K_RIGHT, 0), (pygame.K_ESCAPE, 0))
    app.exit_after = 5.0 + pygame.time.get_ticks() / 1000  # a safety net, should Esc not end it
    code = app.run_settings()
    assert code == 0 and not page.open
    # saved at 61; the second change was left: one Esc only warns, so the safety net ended it
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"round": {"duration_s": 61}}
    assert page.message[1] == "error" and [k for k, _ in page.buttons] == ["save", "revert", "defaults"]


def test_the_page_alone_quits_on_a_second_esc_and_at_once_when_nothing_changed(tmp_path):
    app, clock = make_app(tmp_path)
    pygame.event.clear()
    post_keys((pygame.K_RIGHT, 0), (pygame.K_ESCAPE, 0), (pygame.K_ESCAPE, 0))
    app.exit_after = 5.0 + pygame.time.get_ticks() / 1000
    s = time.perf_counter()
    assert app.run_settings() == 0
    assert time.perf_counter() - s < 2.0 and not (tmp_path / "settings.json").exists()

    app, clock = make_app(tmp_path)
    pygame.event.clear()
    post_keys((pygame.K_ESCAPE, 0))
    app.exit_after = 5.0 + pygame.time.get_ticks() / 1000
    s = time.perf_counter()
    assert app.run_settings() == 0 and time.perf_counter() - s < 2.0


# -- the LiDAR tab (the lidar tool's settings, config/lidar.json) -----------------------------------
def lidar_app(tmp_path):
    lidar_file = tmp_path / "lidar.json"
    lidar_file.write_text(json.dumps({"shape": "circle", "radius": 1500, "bg_margin": 40, "mode": "ball"}), encoding="utf-8")
    return make_app(tmp_path, {"input": {"sources": [], "lidar": {"settings_file": str(lidar_file)}}})[0], lidar_file


def test_the_lidar_tab_changes_the_lidar_and_saves_only_into_its_file(tmp_path):
    app, lidar_file = lidar_app(tmp_path)
    page = app.settings
    try:
        assert page.lidar is not None and page.lidar.radius == 1500
        app.open_settings()
        tab, row = find(page, ("lidar", "bg_margin"))
        page.select_tab(tab)
        page.row = page.items.index(page.pages[tab][1][row])
        key(app, pygame.K_RIGHT)
        assert page.lidar.bg_margin == 45  # at once, one step of the lidar tool
        assert page.changed() == [("lidar", "bg_margin")]
        app.draw(app.clock.now())
        assert page.press("save") == "saved"
        assert json.loads(lidar_file.read_text(encoding="utf-8"))["bg_margin"] == 45
        assert not (tmp_path / "settings.json").exists()  # nothing of the game changed
        assert page.changed() == []
    finally:
        app.shutdown()


def test_the_lidar_area_rows_follow_the_shape_and_revert_puts_it_back(tmp_path):
    app, lidar_file = lidar_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        page.select_tab(find(page, ("lidar", "shape"))[0])
        assert ("lidar", "radius") in [it.key for it in page.items] and ("lidar", "width") not in [it.key for it in page.items]
        app.draw(app.clock.now())
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "option", (0, "rectangle")).center, button=1)
        assert page.lidar.shape == "rectangle"
        keys = [it.key for it in page.items]
        assert ("lidar", "width") in keys and ("lidar", "height") in keys and ("lidar", "radius") not in keys
        app.draw(app.clock.now())
        assert page.press("revert") == "close"
        assert page.lidar.shape == "circle" and page.changed() == []
        assert json.loads(lidar_file.read_text(encoding="utf-8"))["shape"] == "circle"
    finally:
        app.shutdown()


def test_without_a_lidar_file_there_is_no_lidar_tab(tmp_path):
    app, _ = make_app(tmp_path, {"input": {"sources": [], "lidar": {"settings_file": str(tmp_path / "none.json")}}})
    try:
        assert app.settings.lidar is None and all(extra != "lidar" for _, _, extra in app.settings.pages)
    finally:
        app.shutdown()


def test_every_slider_says_what_its_low_and_high_end_do():
    for it in all_items() + [it for _, items, _ in S.lidar_pages() for it in items]:
        if it.switch or it.options:
            continue
        low, high = S.hint(it.key)
        assert low and high and low != high, it.key


def test_a_resolution_button_sets_the_picture_size_landscape_or_portrait(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        tab, row = find(page, ("display", "resolution"))
        page.select_tab(tab)
        app.draw(app.clock.now())
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "option", (row, [800, 1280])).center, button=1)
        assert app.cfg.display.resolution == [800, 1280]
        assert page.changed() == [("display", "resolution")]  # one change, not three
        page.row = row + 1  # the width slider changes the copy, never the button's own list
        key(app, pygame.K_RIGHT)
        assert app.cfg.display.resolution == [801, 1280] and S.RESOLUTIONS[4][0] == [800, 1280]
        app.draw(app.clock.now())
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "option", (row, [1920, 1080])).center, button=1)
        assert page.press("save") == "saved"
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert saved == {"display": {"resolution": [1920, 1080]}}
    finally:
        app.shutdown()


def test_the_width_and_height_are_typed_in_their_boxes(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        tab, row = find(page, ("display", "resolution", 0))
        page.select_tab(tab)
        app.draw(app.clock.now())
        mouse(app, pygame.MOUSEBUTTONDOWN, hit_rect(page, "entry", row).center, button=1)
        for k in (pygame.K_1, pygame.K_3, pygame.K_6, pygame.K_6):  # digits do not change the tab
            key(app, k)
        assert page.tab == tab and app.cfg.display.resolution[0] == 768  # not yet: Enter sets it
        key(app, pygame.K_RETURN)
        assert app.cfg.display.resolution[0] == 1366 and page._edit is None
        page.row = row + 1
        key(app, pygame.K_RETURN)  # Enter on the height row starts typing too
        key(app, pygame.K_KP5)
        key(app, pygame.K_ESCAPE)  # Esc leaves it as it was, and the page stays open
        assert app.cfg.display.resolution[1] == 1024 and page.open
        key(app, pygame.K_RETURN)
        key(app, pygame.K_9)
        key(app, pygame.K_RETURN)
        assert app.cfg.display.resolution[1] == S.MIN_PIXELS  # too small for a window
    finally:
        app.shutdown()


def test_the_picture_scale_shrinks_the_wall_in_the_middle_of_the_same_window(tmp_path):
    app, clock = make_app(tmp_path)
    try:
        screen, full = app.screen, app.vp.rect.copy()
        app.open_settings()
        S.put(app.cfg.raw(), ("display", "scale"), 0.5)
        app.close_settings()
        assert app.screen is screen  # the window was not opened again
        assert app.vp.rect.w == round(full.w / 2) and app.vp.rect.center == full.center
        assert app.renderer.vp_menu.ppc == pytest.approx(app.vp.ppc)
    finally:
        app.shutdown()


def test_time_and_ball_limits_have_switches_and_one_always_stays_on(tmp_path):
    app, clock = make_app(tmp_path)
    page = app.settings
    try:
        app.open_settings()
        tab, row = find(page, ("round", "time_limit"))
        page.select_tab(tab)
        keys = [it.key for it in page.items]
        assert keys[keys.index(("round", "time_limit")) + 1] == ("round", "duration_s")
        assert keys[keys.index(("round", "ball_limit")) + 1] == ("round", "balls")
        assert page.items[keys.index(("round", "duration_s"))].entry and page.items[keys.index(("round", "balls"))].entry
        assert app.cfg.round.time_limit and not app.cfg.round.ball_limit  # as it ships: time only
        page._nudge(row, 1)  # time off, with no ball limit: the ball limit comes on
        assert not app.cfg.round.time_limit and app.cfg.round.ball_limit and page.message[1] == "ok"
        page._nudge(keys.index(("round", "ball_limit")), 1)  # ball off: the time comes back
        assert app.cfg.round.time_limit and not app.cfg.round.ball_limit
        b = keys.index(("round", "balls"))  # type the number of balls
        page.row = b
        key(app, pygame.K_RETURN)
        for k in (pygame.K_1, pygame.K_5, pygame.K_RETURN):
            key(app, k)
        assert app.cfg.round.balls == 15
        page.draw(app.screen, app.vp)
    finally:
        app.shutdown()


def test_every_page_fits_above_the_buttons():
    raw = game_cfg().raw()
    for title, items, extra in S.pages(raw):
        if extra:  # pages with a drawing above or below their rows are checked by eye in the gallery
            continue
        assert S.TOP + sum(it.height for it in items) <= 400 - 40, title
