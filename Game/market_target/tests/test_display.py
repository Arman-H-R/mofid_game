"""The screen: the canvas size from display.resolution, filling the whole screen (stretched,
no black bars), the mouse in a stretched window, and the Screen tab of the settings page."""
import json
import pygame
import pytest

from market_pulse import app as A
from market_pulse import config
from market_pulse.ui import settings as S
from test_settings import find, key, make_app


@pytest.mark.parametrize("res, size", [
    ([0, 0], None),
    ([1200, 1600], (1200, 1600)),
    ([1200, 0], (1200, 1600)),  # the other one follows the wall's shape (3:4)
    ([0, 1600], (1200, 1600)),
    ([40, 0], None),  # too small: automatic
])
def test_canvas_size(res, size):
    assert A.canvas_size(res, 300 / 400) == size


def test_auto_is_a_size_of_zeros_and_fill_is_off_by_default(tmp_path):
    file = tmp_path / "s.json"
    file.write_text(json.dumps({"display": {"resolution": "auto"}}), encoding="utf-8")
    cfg, warnings = config.load(file)
    assert not warnings and cfg.display.resolution == [0, 0] and cfg.display.fill_screen is False
    file.write_text(json.dumps({"display": {"resolution": [-5, 100]}}), encoding="utf-8")
    cfg, warnings = config.load(file)
    assert warnings and cfg.display.resolution == [0, 0]


DESKTOPS = [(1920, 1080), (1366, 768), (1280, 1024), (1080, 1920), (1536, 2048)]


@pytest.mark.parametrize("desk", DESKTOPS)
@pytest.mark.parametrize("fullscreen", [True, False])
@pytest.mark.parametrize("fill", [True, False])
def test_nothing_is_ever_stretched(tmp_path, monkeypatch, desk, fullscreen, fill):
    """Every mode opens a SCALED canvas (SDL scales it evenly, with bars), a window has the
    wall's shape, and the wall inside the canvas keeps 3:4 with one scale for x and y."""
    app, _ = make_app(tmp_path, {"display": {"fullscreen": fullscreen, "fill_screen": fill, "resolution": [0, 0]}})
    opened = []
    monkeypatch.delenv("SDL_VIDEODRIVER")
    monkeypatch.setenv("PYGAME_FORCE_SCALE", "unset")  # put back after the test (the game sets it)
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [desk])
    monkeypatch.setattr(pygame.display, "set_mode", lambda size, flags=0, **kw: opened.append((size, flags))
                        or pygame.Surface(size))
    screen = app._open_display()
    (w, h), flags = opened[-1]
    assert flags & pygame.SCALED and bool(flags & pygame.FULLSCREEN) == fullscreen
    assert A.os.environ.get("PYGAME_FORCE_SCALE") == ("photo" if fill else None)  # smooth or pixel; even either way
    if fullscreen:
        assert (w, h) == desk  # the display's own pixels, 1:1
    else:
        assert abs(w / h - 0.75) < 0.01 and w <= desk[0] and h <= desk[1]
    vp = A.Viewport(*screen.get_size(), 300, 400)
    assert abs(vp.rect.w / vp.rect.h - 0.75) < 0.005
    assert abs(vp.rect.w - vp.ppc * 300) < 1 and abs(vp.rect.h - vp.ppc * 400) < 1  # one scale
    assert vp.rect.centerx in (w // 2, w // 2 - 1, (w + 1) // 2) and abs(vp.rect.centery - h / 2) <= 1
    for x, y in ((25, 25), (150, 200), (275, 375)):  # a lidar hit in wall cm lands where it is drawn
        nx, ny = vp.to_norm(*vp.px(x, y))
        assert abs(nx * 300 - x) < 0.5 and abs(ny * 400 - y) < 0.5


def test_the_screen_tab_changes_the_canvas_when_the_page_closes(tmp_path):
    app, _ = make_app(tmp_path)
    key(app, pygame.K_F5)
    tab, row = find(app.settings, ("display", "resolution", 0))
    app.settings.select_tab(tab)
    app.settings.row = row
    for _ in range(4):
        key(app, pygame.K_LEFT, pygame.KMOD_SHIFT)  # 768 -> 748: the typed box steps 1, Shift 5
    assert app.cfg.display.resolution == [748, 1024] and app.screen.get_size() == (768, 1024)
    app.frame()  # drawn with the notes of the tab
    key(app, pygame.K_s, pygame.KMOD_CTRL)
    assert not app.settings.open and app.screen.get_size() == (748, 1024)
    assert app.vp.win_w == 748 and app.renderer.vp is app.vp
    app.frame()
    doc = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert doc["display"] == {"resolution": [748, 1024]}
    cfg, warnings = config.load(tmp_path / "settings.json")
    assert not warnings and cfg.display.resolution == [748, 1024]
    assert not app.apply_display()  # nothing new


def test_every_screen_row_shows_its_value():
    rows = {it.key: it for _, items, _ in S.pages(config.DEFAULTS) for it in items}
    width = rows[("display", "resolution", 0)]
    assert S.shown(width, 0) == "خودکار" and S.shown(width, 1920) == "1920 پیکسل"
    assert rows[("display", "fill_screen")].switch and rows[("display", "fullscreen")].switch


def test_f11_switches_full_screen_now_and_the_page_can_save_it(tmp_path):
    app, _ = make_app(tmp_path)
    opened = []
    app._open_display = lambda: opened.append(app.cfg.display.fullscreen) or pygame.Surface((768, 1024))
    start = app.cfg.display.fullscreen
    key(app, pygame.K_F11)
    assert opened == [not start] and app.cfg.display.fullscreen is not start
    assert ("display", "fullscreen") in app.settings.changed()  # unsaved until F5 saves it
    key(app, pygame.K_F11)
    assert opened == [not start, start] and not app.settings.changed()


# -- the game in the screen's shape (display.fill_screen) --------------------------------------
def wide_app(tmp_path, fill=True):
    return make_app(tmp_path, {"display": {"fill_screen": fill, "resolution": [1920, 1080]}})


def test_fill_gives_the_wall_the_screens_shape_and_keeps_the_menus_in_the_middle(tmp_path):
    app, _ = wide_app(tmp_path)
    W = app.cfg.wall.width_cm
    assert abs(W - 400 * 1920 / 1080) < 0.1 and app.cfg.wall.layout_width_cm == 300
    assert app.vp.rect == pygame.Rect(0, 0, 1920, 1080)  # play fills the screen, no bars
    assert abs(app.vp.rect.w / app.vp.rect.h - W / 400) < 0.002  # and nothing is stretched
    menu = app.renderer.vp_menu
    assert menu.ppc == app.vp.ppc and menu.rect.h == 1080 and abs(menu.rect.centerx - 960) <= 1
    assert app.settings.W == 300
    left = config.layout_left(app.cfg)
    assert abs(left - (W - 300) / 2) < 1e-6
    sb = app.cfg.layout.start_button  # the start square, where the menu draws it
    assert app.flow.start_button_hit(sb.x_cm + left, sb.y_cm) and not app.flow.start_button_hit(sb.x_cm, sb.y_cm)
    rules_w = app.cfg.wall.width_cm  # boxes spawn over the whole wall
    assert rules_w == W


def test_without_fill_the_wall_stays_three_by_four(tmp_path):
    app, _ = wide_app(tmp_path, fill=False)
    assert app.cfg.wall.width_cm == 300 and config.layout_left(app.cfg) == 0
    assert app.renderer.vp_menu is app.vp and app.vp.rect.w == 810  # bars at the sides


def test_every_screen_draws_on_the_wide_wall(tmp_path):
    from market_pulse.app import Paths, render_gallery
    from conftest import ROOT, game_cfg
    cfg = game_cfg({"display": {"fill_screen": True, "resolution": [1280, 720]}, "camera": {"enabled": True},
                    "input": {"sources": []}, "web": {"enabled": False}})
    files = render_gallery(cfg, Paths(ROOT), tmp_path / "g")
    assert len(files) > 15
    attract = pygame.image.load(str(files[0]))
    assert attract.get_size() == (1280, 720)
    from market_pulse.ui import theme
    assert tuple(attract.get_at((3, 360)))[:3] == tuple(theme.DARK_BLUE)  # the menu's background, not a bar


def test_a_calibration_for_another_wall_size_is_not_used(tmp_path):
    import queue
    from market_pulse.input.lidar.source import LidarSource
    old = [{"wall_cm": [25, 25], "lidar_mm": [-138, 1790]}, {"wall_cm": [275, 25], "lidar_mm": [600, 1790]},
           {"wall_cm": [275, 375], "lidar_mm": [638, 947]}, {"wall_cm": [25, 375], "lidar_mm": [-114, 918]}]
    cfg = config.Section({**config.DEFAULTS["input"]["lidar"], "calibration_points": old})
    same = LidarSource(queue.SimpleQueue(), cfg, tmp_path / "none.json", 300, 400)
    assert same.tuning.calibrated
    wider = LidarSource(queue.SimpleQueue(), cfg, tmp_path / "none.json", 711.1, 400)
    assert not wider.tuning.calibrated  # the crosses were elsewhere: calibrate again


def test_calibrating_a_wide_wall_sees_every_corner(tmp_path):
    """The tool's circle (config/lidar.json) covered the 3:4 wall; on a wall in the screen's shape
    the top corners were outside it, so only the first cross could be hit."""
    import numpy as np
    from market_pulse.input.lidar.wallmap import GameTuning
    from conftest import ROOT
    tuning = GameTuning(str(ROOT / "config" / "lidar.json"), 711.1, 400, 15, 8000)
    assert tuning.load() and tuning.shape == "circle"
    corners = [(-744, 1740), (1206, 1740), (1244, 900), (-720, 870)]  # mm: the full-screen 3:4 calibration's crosses, moved out to the wide wall
    x, y = (np.array(v, dtype=float) for v in zip(*corners))
    assert not tuning.in_area(x, y, np.hypot(x, y)).all()  # the tool's circle alone misses some
    tuning.capture = True
    assert tuning.in_area(x, y, np.hypot(x, y)).all()
    far = np.array([5000.0]), np.array([1000.0])
    assert not tuning.in_area(*far, np.hypot(*far))[0]  # still not the whole field
    assert tuning.reach() >= max(np.hypot(x, y))


def test_a_stray_hit_beside_the_wall_is_not_the_next_cross():
    """The real calibration on the wide wall: crosses 1-3 were taken, then a point 2 m right of
    the wall came half a second before the real 4th cross and the fit failed (457 cm off)."""
    from market_pulse.input.lidar.capture import Capture
    real = [(-791.4, 1848.7), (1172.4, 1837.9), (1235.7, 910.7)]
    hits = real + [(3158.9, 1809.6), (-777.0, 895.0)]
    cap = Capture(711.1, 400, since=0.0)
    cap.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(hits)])
    assert cap.done and cap.strays == 1 and cap.seen[3] == (-777.0, 895.0)
    assert cap.fit().worst_cm < 8


def test_the_hit_margin_is_on_the_round_tab_and_counts_at_once(tmp_path):
    from market_pulse.game.rules import Round
    app, _ = make_app(tmp_path)
    tab, row = find(app.settings, ("input", "ball_radius_cm"))
    assert app.settings.pages[tab][0] == "دور بازی"  # the boxes tab is full
    rnd = Round(app.cfg)
    key(app, pygame.K_F5)
    app.settings.select_tab(tab)
    app.settings.row = row
    before = app.cfg.input.ball_radius_cm
    key(app, pygame.K_RIGHT)
    assert app.cfg.input.ball_radius_cm == before + 1 and rnd.tolerance == before + 1
    app.frame()  # the boxes with their hit line


def test_recalibrating_keeps_to_the_wall_so_the_stray_spot_is_not_a_cross():
    """Live: with a calibration in use, C took a stray point 2 m right of the wall as cross 1."""
    import numpy as np
    from market_pulse.input.lidar.capture import targets
    from market_pulse.input.lidar.wallmap import GameTuning, WallMap
    from conftest import ROOT
    tuning = GameTuning(str(ROOT / "config" / "lidar.json"), 711.1, 400, 15, 8000)
    tuning.load()
    seen = [(-775.9, 1901.8), (1216.3, 1911.3), (1241.6, 922.9), (-769.4, 905.2)]  # the 1.7 cm calibration
    tuning.fitted = WallMap.fit(list(zip(seen, targets(711.1, 400))))
    tuning.capture = True
    x, y = (np.array(v) for v in zip(*seen, (3174.0, 1822.0)))
    inside = tuning.in_area(x, y, np.hypot(x, y))
    assert inside[:4].all() and not inside[4]


@pytest.mark.parametrize("size, wall", [((800, 1280), (300, 480)), ((1080, 1920), (300, 533.3)),
                                        ((1920, 1080), (711.1, 400)), ((768, 1024), (300, 400))])
def test_fill_screen_takes_a_portrait_or_landscape_shape_when_the_size_changes_on_the_page(tmp_path, size, wall):
    app, clock = make_app(tmp_path, {"display": {"fill_screen": True, "resolution": [1280, 800]}})
    try:
        assert (app.cfg.wall.width_cm, app.cfg.wall.height_cm) == (640, 400)
        app.open_settings()
        S.put(app.cfg.raw(), ("display", "resolution"), list(size))
        app.close_settings()
        W, H = app.cfg.wall.width_cm, app.cfg.wall.height_cm
        assert (W, H) == pytest.approx(wall, abs=0.1)
        assert app.screen.get_size() == size and app.vp.rect.size == size  # the whole picture, no bars
        assert (app.renderer.W, app.renderer.H) == (W, H) and app.input.wall_w == W and app.input.wall_h == H
        menu = app.renderer.vp_menu  # the 3:4 menus in the middle, at the same scale
        assert menu.ppc == pytest.approx(app.vp.ppc)
        assert all(abs(a - b) <= 1 for a, b in zip(menu.rect.center, app.vp.rect.center))
        sb = app.cfg.layout.start_button  # the start square is hit where it is drawn
        x, y = config.layout_left(app.cfg) + sb.x_cm, config.layout_top(app.cfg) + sb.y_cm
        assert all(abs(a - b) <= 1 for a, b in zip(app.vp.px(x, y), menu.px(sb.x_cm, sb.y_cm)))
        assert app.flow.start_button_hit(x, y)
        app.flow.request_start(app.clock.now())
        for _ in range(3):
            app.frame()
        app.open_settings()
        app.settings.select_tab(0)
        app.frame()
    finally:
        app.shutdown()


def test_a_round_on_a_portrait_wall_spreads_its_boxes_over_the_whole_height(tmp_path):
    app, clock = make_app(tmp_path, {"display": {"fill_screen": True, "resolution": [800, 1280]}})
    try:
        assert app.cfg.wall.height_cm == 480
        app.flow.request_start(app.clock.now())
        app.flow.submit_registration(app.clock.now())
        for _ in range(400):
            clock.t += 1 / 60
            app.frame()
            if app.flow.round is not None and app.flow.round.targets:
                break
        rnd = app.flow.round
        assert rnd is not None and rnd.targets
        ys = [tg.center_at(app.clock.now())[1] for tg in rnd.targets]
        assert max(ys) <= 480 - app.cfg.layout.footer_height_cm
    finally:
        app.shutdown()
