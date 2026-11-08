"""Application: display, main loop, operator keys and wiring of all parts."""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pygame

from .audio import Audio
from .camera import PlayerCam
from .config import Section
from .game import flow as F
from .game.results import ResultsStore, WebhookSender
from .input.events import Control
from .input.lidar.capture import Capture
from .input.lidar.tuning import Settings
from .input.manager import InputManager
from .ui import overlays
from .ui.assets import AssetLibrary, load_tokens
from .ui.draw import Sprites
from .ui.effects import Effects
from .ui.screens import Renderer
from .ui.settings import SettingsPage, write_changes
from .ui.text import FontBook, Text
from .ui.theme import Viewport
from .web import Panel

log = logging.getLogger(__name__)
SETTINGS_EVENTS = (pygame.KEYDOWN, pygame.TEXTINPUT, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION,
                   pygame.MOUSEWHEEL)
MISSES_FOR_UNDO = 3  # hits in a row that miss the next cross: say Backspace takes the last one back


def canvas_size(resolution, aspect: float) -> tuple[int, int] | None:
    """display.resolution -> the canvas size, or None to take it from the display. A size below
    64 counts as 0; one of the two 0 follows the wall's shape (aspect = width / height)."""
    w, h = (int(v) if v >= 64 else 0 for v in resolution)
    if w and h:
        return w, h
    if w:
        return w, max(64, int(round(w / aspect)))
    if h:
        return max(64, int(round(h * aspect))), h
    return None


def fit_wall(cfg: Section) -> bool:
    """display.fill_screen: the wall takes the display's shape, so play fills the whole screen
    with nothing stretched: wider on a landscape screen, taller on a portrait one. The 3:4 layout
    of the menus (wall.layout_width_cm x layout_height_cm) stays in the middle, at the same scale
    (config.layout_left, layout_top). Off: the wall is that layout, with bars. At the start, and
    again when the display changes (App.apply_display). Returns True when the wall's size changed."""
    wall = cfg.raw()["wall"]
    lw = float(wall.setdefault("layout_width_cm", wall["width_cm"]))
    lh = float(wall.setdefault("layout_height_cm", wall["height_cm"]))
    old = (float(wall["width_cm"]), float(wall["height_cm"]))
    d = cfg.display
    width, height = lw, lh
    if d.fill_screen:
        res = canvas_size(d.resolution, 1.0)
        shape = None
        if res and 0 not in d.resolution:
            shape = res[0] / res[1]
        elif os.environ.get("SDL_VIDEODRIVER") != "dummy":  # headless with no size given: nothing to fit
            sizes = pygame.display.get_desktop_sizes() or [(1, 1)]
            w, h = sizes[min(d.display_index, len(sizes) - 1)]
            shape = w / h
        if shape is not None and shape > lw / lh * 1.003:
            width = lh * shape  # exact: the menus and play share one scale
        elif shape is not None and shape < lw / lh / 1.003:
            height = lw / shape
    if (width, height) == old:
        return False
    wall["width_cm"], wall["height_cm"] = width, height
    log.info("display.fill_screen: the wall is %.0f x %.0f cm; the menus keep %.0f x %.0f cm", width, height, lw, lh)
    return True


@dataclass(frozen=True)
class Paths:
    root: Path  # results and logs are written here
    bundle: Path | None = None  # where the artwork is, if not under root (inside the .exe)

    @property
    def assets(self) -> Path:
        return (self.bundle or self.root) / "assets"

    @property
    def fonts(self) -> Path:
        return self.assets / "fonts"

    @property
    def images(self) -> Path:
        return self.assets / "images"

    @property
    def products(self) -> Path:
        return self.assets / "products"

    @property
    def sounds(self) -> Path:
        return self.assets / "sounds"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


class App:
    MAX_ERRORS_PER_MINUTE = 5
    HEALTH_LOG_S = 600.0  # a one-line status in the log every 10 minutes

    def __init__(
        self,
        cfg: Section,
        paths: Paths,
        warnings: list[str] | None = None,
        clock_source: Callable[[], float] | None = None,
        start_inputs: bool = True,
        settings_file: Path | None = None,
        settings_source: Path | None = None,
    ):
        """``settings_file``: where the settings page (F5) saves; ``settings_source``: the file the
        settings were read from, if that is another one (the copy inside the .exe)."""
        self.cfg = cfg
        self.paths = paths
        self.warnings = list(warnings or [])
        pygame.init()
        # colour tokens first: the SVGs are recoloured while they are read
        problems = load_tokens(paths.assets)
        self.assets, more = AssetLibrary.load(paths.assets)
        for msg in problems + more:
            log.warning("assets: %s", msg)
        self.warnings += problems + more
        fit_wall(cfg)
        self.screen = self._open_display()
        self._display_open = self._display_values()  # what the window was opened with
        self.vp = Viewport(*self.screen.get_size(), cfg.wall.width_cm, cfg.wall.height_cm, cfg.display.scale)
        log.info("canvas %dx%d, wall area %s, %.2f px/cm", *self.screen.get_size(), self.vp.rect, self.vp.ppc)

        self.text = Text(FontBook(paths.fonts, cfg.fonts.raw()), cfg.ui.digits == "fa")
        self.sprites = Sprites(self.assets)
        self.sprites.set_products(paths.products, cfg.raw()["products"])  # the 13 product tiles
        missing = [p["file"] for p in cfg.raw()["products"] if not (paths.products / p["file"]).is_file()]
        if missing:
            msg = f"assets: product tiles not found in {paths.products}: {', '.join(missing)} (plain tiles drawn)"
            log.warning("%s", msg)
            self.warnings.append(msg)
        self.effects = Effects(self.sprites, self.text)
        self.renderer = Renderer(cfg, paths.images, self.text, self.sprites, self.effects)
        self.renderer.set_viewport(self.vp)
        if start_inputs and cfg.flow.intro_s > 0:  # the Mofid logo while the rest loads
            self.renderer.draw_loading(self.screen)
            pygame.display.flip()
            self.renderer.prewarm(budget_s=30.0)  # the box sprites now, so the logo's zoom does not stutter

        self.clock = F.Clock(clock_source) if clock_source else F.Clock()
        self.store = ResultsStore(paths.root / cfg.results.log_file, cfg.results.leaderboard_size)
        url = cfg.results.webhook_url.strip()
        self.webhook = WebhookSender(url, cfg.results.webhook_timeout_s) if url else None
        self.flow = F.Flow(cfg, self.store, self.webhook.submit if self.webhook else None, np.random.default_rng())
        self.input = InputManager(cfg, self.clock.to_game, roots=(paths.root, paths.bundle))
        self.audio = Audio(cfg.audio.enabled, cfg.audio.volume, paths.sounds)
        self.board = self.store.leaderboard()

        self.debug = bool(cfg.debug.overlay)
        self.calibration = False
        self.capture: Capture | None = None  # the lidar's calibration points being taken (F2, C)
        self.settings = SettingsPage(cfg, self.text, self.sprites, settings_file, settings_source, self._settings_changed,
                                     self._lidar_settings())
        self._settings_paused = False
        self.running = False
        self.exit_after: float | None = None  # seconds; for unattended smoke tests
        self.frame_errors = 0
        self._error_times: deque = deque()
        self._last_now = self.clock.now()
        self._esc_at = -10.0
        self._notice = ("", 0.0)
        if self.warnings:
            self._notify(f"{len(self.warnings)} config warning(s) - see logs/market_pulse.log or press F3", 15)
        self._fps = 0.0
        self._frame_ms = 0.0
        self._typing = False
        self._update_cursor()
        self.panel = Panel(cfg, self.store, paths.assets) if start_inputs and cfg.web.enabled else None
        self.camera: PlayerCam | None = None
        if start_inputs and cfg.camera.enabled and cfg.results.get("show_photo", False):  # only the photo card uses it
            dirs = [base / cfg.camera.model_dir for base in (paths.root, paths.bundle) if base is not None]
            self.camera, problem = PlayerCam.create(cfg.camera, dirs)
            if problem:
                log.warning("%s", problem)
                self.warnings.append(problem)
        if start_inputs:
            self.input.start()
        if self.panel is not None:
            if self.panel.start():
                self._notify(f"Phone page: {self.panel.url}", 30)
            else:
                self._notify(f"Phone page could not start: port {cfg.web.port} is in use", 30)

    # -- display ------------------------------------------------------------------------------
    def _open_display(self) -> pygame.Surface:
        d = self.cfg.display
        sizes = pygame.display.get_desktop_sizes() or [(1080, 1440)]
        idx = d.display_index
        if idx >= len(sizes):
            log.error("display.display_index=%d but only %d display(s) found; using 0", idx, len(sizes))
            idx = 0
        desk_w, desk_h = sizes[idx]
        wall_w, wall_h = self.cfg.wall.width_cm, self.cfg.wall.height_cm
        res = canvas_size(d.resolution, wall_w / wall_h)
        if os.environ.get("SDL_VIDEODRIVER") == "dummy":  # headless rendering (gallery, tests)
            size = res or (1536, 2048)
            screen = pygame.display.set_mode(size)
            log.info("headless canvas %dx%d", *size)
            return screen
        # Nothing is ever stretched: the wall keeps its shape in the canvas (Viewport), and SCALED
        # scales the canvas evenly (one factor for x and y) into the screen or window, with bars
        # where the shapes differ: as large as fits when maximized or full screen. fill_screen
        # makes that scaling smooth; off, it is pixel by pixel (pygame's own).
        if d.fill_screen:
            os.environ["PYGAME_FORCE_SCALE"] = "photo"
        else:
            os.environ.pop("PYGAME_FORCE_SCALE", None)
        if d.fullscreen:
            size = res or (desk_w, desk_h)  # auto: the display's own pixels, the wall centred
            flags = pygame.FULLSCREEN | pygame.SCALED
        else:
            h = int(min(desk_h * d.windowed_height_fraction, desk_w * 0.95 * wall_h / wall_w))  # also on a tall display
            usual = (int(h * wall_w / wall_h), h)
            if res:
                fit = min(1.0, desk_w * 0.95 / res[0], desk_h * 0.9 / res[1])
                size = (int(res[0] * fit), int(res[1] * fit))
            else:
                size = usual
            flags = pygame.SCALED | pygame.RESIZABLE
        pygame.display.set_caption("Market Pulse · نبض بازار · Mofid")
        for vsync in ((1, 0) if d.vsync else (0,)):
            try:
                screen = pygame.display.set_mode(size, flags, display=idx, vsync=vsync)
                break
            except pygame.error as exc:
                log.warning("set_mode(%s, vsync=%d) failed: %s", size, vsync, exc)
        else:
            screen = pygame.display.set_mode(size)
        self._set_icon()
        log.info("display %d: desktop %dx%d, canvas %dx%d, fullscreen=%s, fill_screen=%s", idx, desk_w, desk_h,
                 *screen.get_size(), d.fullscreen, d.fill_screen)
        return screen

    def _display_values(self) -> tuple:
        d = self.cfg.display
        return d.fullscreen, d.fill_screen, tuple(d.resolution), d.scale

    def toggle_fullscreen(self) -> None:
        """F11: full screen or a window, now. The settings page (F5) can save it."""
        raw = self.cfg.raw()["display"]
        raw["fullscreen"] = not raw["fullscreen"]
        self.apply_display()
        self._notify("Full screen (F11: a window)" if raw["fullscreen"] else "A window (F11: full screen)", 3)

    def apply_display(self) -> bool:
        """The display settings changed on the settings page: open the window again with them."""
        now = self._display_values()
        if now == self._display_open:
            return False
        if now[:3] != self._display_open[:3] and fit_wall(self.cfg):
            self._wall_changed()
        if now[:3] == self._display_open[:3]:  # only the picture's scale: the same window
            log.info("display.scale changed to %.0f %%", self.cfg.display.scale * 100)
        elif os.environ.get("SDL_VIDEODRIVER") != "dummy":
            log.info("display settings changed: opening the window again")
            # A window turned full screen by set_mode kept showing its last frame (Windows 11,
            # SCALED): a new window, as at the start, shows every frame.
            pygame.display.quit()
            pygame.display.init()
            if self._typing:  # the new window starts with SDL's text input on and no key repeat
                pygame.key.start_text_input()
                pygame.key.set_repeat(350, 40)
            else:
                pygame.key.stop_text_input()
        if now[:3] != self._display_open[:3]:
            self.screen = self._open_display()
        self._display_open = now
        self.vp = Viewport(*self.screen.get_size(), self.cfg.wall.width_cm, self.cfg.wall.height_cm, self.cfg.display.scale)
        log.info("canvas %dx%d, wall area %s, %.2f px/cm", *self.screen.get_size(), self.vp.rect, self.vp.ppc)
        self.renderer.set_viewport(self.vp)
        self._update_cursor()
        return True

    def _wall_changed(self) -> None:
        """display.fill_screen gave the wall another shape: everything that keeps its size takes the
        new one. A round being played ends (its boxes are on the old wall); the lidar keeps its
        calibration only if it was made for this size."""
        W, H = self.cfg.wall.width_cm, self.cfg.wall.height_cm
        if self.flow.round is not None and not self.flow.round.finished:
            self.flow.reset(self.clock.now())
        if self.capture is not None:
            self.stop_capture()
        self.renderer.set_wall()
        if not self.input.set_wall(W, H):
            self._notify("The wall has a new shape: calibrate the LiDAR again (F2 then C)", 10)

    def _set_icon(self) -> None:
        mark = self.assets.render("logo.mark", 56)
        if mark is not None:
            icon = pygame.Surface((64, 64), pygame.SRCALPHA)
            icon.blit(mark, mark.get_rect(center=(32, 32)))
            pygame.display.set_icon(icon)
            return
        path = self.paths.images / "logo_fa_stacked.png"
        try:
            logo = pygame.image.load(str(path))
        except (pygame.error, FileNotFoundError):
            return
        bull = logo.subsurface(pygame.Rect(0, 0, logo.get_width(), int(logo.get_height() * 0.5)))
        side = bull.get_width()
        icon = pygame.Surface((side, side), pygame.SRCALPHA)
        icon.blit(bull, (0, (side - bull.get_height()) // 2))
        pygame.display.set_icon(pygame.transform.smoothscale(icon, (64, 64)))

    def _update_cursor(self) -> None:
        hide = self.cfg.display.fullscreen and self.cfg.display.hide_cursor
        typing = self.flow.state == F.REGISTER  # the Start button can be clicked
        pygame.mouse.set_visible(self.input.simulation or self.calibration or self.debug or self.settings.open or typing
                                 or not hide)

    def _notify(self, msg: str, seconds: float) -> None:
        self._notice = (msg, time.monotonic() + seconds)

    # -- main loop ------------------------------------------------------------------------------
    def run(self) -> int:
        self.running = True
        pg_clock = pygame.time.Clock()
        fps = self.cfg.display.fps
        code = 0
        if self.cfg.flow.intro_s > 0:
            self.renderer.start_boot(self.clock.now())
        started = last_health = prev = time.perf_counter()
        frames = dropped = 0
        while self.running:
            pg_clock.tick(fps)
            t0 = time.perf_counter()
            try:
                self.frame()
            except Exception:  # keep an unattended installation alive
                code = self._recover()
                if code:
                    break
            t1 = time.perf_counter()
            self._frame_ms = (t1 - t0) * 1000.0
            self._fps = pg_clock.get_fps()
            frames += 1
            if t1 - prev > 1.5 / fps:  # a frame interval clearly longer than one refresh
                dropped += 1
                log.debug("dropped frame: %.1f ms (frame work %.1f ms) in %s", (t1 - prev) * 1000.0,
                          self._frame_ms, self.flow.state)
            prev = t1
            if t1 - last_health >= self.HEALTH_LOG_S:
                last_health = t1
                log.info("health: %.1f fps, %d dropped frames, %d rounds, %d recovered errors",
                         self._fps, dropped, self.flow.rounds_played, self.frame_errors)
            if self.exit_after is not None and t1 - started >= self.exit_after:
                self.running = False
        ran = time.perf_counter() - started
        log.info("stopping after %.0f s: %d frames (%.1f fps average), %d dropped, %d rounds",
                 ran, frames, frames / max(ran, 1e-6), dropped, self.flow.rounds_played)
        self.shutdown()
        return code

    def _recover(self) -> int:
        log.exception("error during a frame; returning to the attract screen")
        self.frame_errors += 1
        now = time.monotonic()
        self._error_times.append(now)
        while self._error_times and now - self._error_times[0] > 60:
            self._error_times.popleft()
        if len(self._error_times) >= self.MAX_ERRORS_PER_MINUTE:
            log.critical("%d errors within a minute; exiting so the launcher can restart the game", len(self._error_times))
            return 2
        try:
            self.clock.resume()
            self.effects.clear()
            self.flow.reset(self.clock.now())
            self.flow.drain_events()
            self._forget_replay()
        except Exception:
            log.exception("recovery failed")
            return 3
        return 0

    def frame(self) -> None:
        now = self.clock.now()
        dt = min(0.1, max(0.0, now - self._last_now))
        self._last_now = now
        for event in pygame.event.get():
            self._handle_event(event, now)
        for ev in self.input.poll():
            if isinstance(ev, Control):
                if ev.kind != "heartbeat":
                    log.info("control %s %s from %s", ev.kind, ev.data, ev.source)
                    if ev.kind == "reset":
                        self.clock.resume()
                    self.flow.handle_control(ev.kind, ev.data, now)
            elif not self.calibration and not self.clock.paused:
                if ev.source == "lidar" and self.cfg.input.lidar.round_only and self.flow.state != F.PLAYING:
                    log.info("lidar hit ignored: no round is being played (state %s)", self.flow.state)
                    continue  # the lidar plays the round only; names and Start come from the phone page
                out = self.flow.handle_impact(ev.x, ev.y, ev.t, now)
                if out is not None:
                    log.info("throw %d: %s %s %s (%.0f, %.0f) +%d", self.flow.round.throws, out.kind, out.tier or "",
                             out.product or "", ev.x, ev.y, out.points)
                    if out.kind == "miss":
                        self._log_nearest_box(ev.x, ev.y, ev.t)
        if self.capture is not None:
            self._feed_capture()
        if self.panel is not None:
            self._serve_panel(now)
        if not self.clock.paused:
            self.flow.update(now)
        for ev in self.flow.drain_events():
            self.renderer.on_event(ev.name, ev.data, self.flow, now)
            self.audio.on_event(ev.name, ev.data)
            if ev.name == "results":
                self.board = self.store.leaderboard()
                log.info("round finished: score %d, rank today %s", ev.data["record"]["score"], ev.data["rank"])
                replay = self.camera.replay(self.camera.finish()) if self.camera is not None else None
                self.renderer.set_replay(ev.data["record"]["round_id"], replay)
            elif ev.name == "go" and self.camera is not None:
                self.camera.begin(now)
            elif ev.name == "hit" and self.camera is not None:
                self.camera.scored(ev.data["outcome"].points)
            elif ev.name == "state":
                self._enter_state(ev.data["state"])
        if self.camera is not None:
            self.camera.tick(now, self.flow.state == F.PLAYING and not self.clock.paused)
        self.effects.update(dt)
        self.renderer.prewarm()
        self.draw(now)
        pygame.display.flip()

    def _serve_panel(self, now: float) -> None:
        answers = []
        for cmd in self.panel.take():
            if cmd.kind == "play":
                ok = self.flow.start_for(cmd.name, now)
                if ok:
                    log.info("phone page: round started for %s", cmd.name)
            else:  # cancel; a round that has ended is on its way to being saved
                ok = self.flow.state in (F.INTRO, F.COUNTDOWN, F.PLAYING)
                if ok:
                    self.clock.resume()
                    self.flow.reset(self.clock.now())
                    log.info("phone page: round cancelled")
            answers.append((cmd, ok))
        self.panel.status = {**self.flow.status(now), "paused": self.clock.paused}
        for cmd, ok in answers:  # last, so a page that asks at once sees the new status
            cmd.answer(ok)

    def _forget_replay(self) -> None:
        """A new or aborted round: the last player's replay must not appear again."""
        if self.camera is not None:
            self.camera.cancel()
        self.renderer.set_replay(None, None)

    def _enter_state(self, state: str) -> None:
        if state in (F.ATTRACT, F.COUNTDOWN):
            self._forget_replay()
        if state == F.ATTRACT:
            self.board = self.store.leaderboard()
        typing = state == F.REGISTER
        if typing != self._typing:  # only then may keys produce text, and Backspace repeat
            self._typing = typing
            if typing:
                pygame.key.start_text_input()
                pygame.key.set_repeat(350, 40)
            else:
                pygame.key.stop_text_input()
                pygame.key.set_repeat()
            self._update_cursor()

    def draw(self, now: float) -> None:
        if self.settings.open:
            self.settings.draw(self.screen, self.renderer.vp_menu, self.renderer.vp.rect)
            return
        msg, until = self._notice
        overlays_info = {
            "paused": self.clock.paused,
            "simulation": self.input.simulation,
            "sensor_offline": self.input.sensor_offline(),
            "leaderboard": self.board,
            "notice": msg if time.monotonic() < until else "",
        }
        self.renderer.draw(self.screen, self.flow, now, overlays_info)
        if self.calibration:
            overlays.draw_calibration(self.screen, self.vp, self.text, self.cfg, self.input.recent,
                                      self.capture, self.input.lidar)
        if self.debug:
            overlays.draw_debug(
                self.screen,
                self.vp,
                self.text,
                {
                    "fps": self._fps,
                    "frame_ms": self._frame_ms,
                    "state": self.flow.state,
                    "paused": self.clock.paused,
                    "simulation": self.input.simulation,
                    "round": self.flow.round,
                    "inputs": self.input.status(),
                    "filter": self.input.filter,
                    "accepted": self.input.accepted,
                    "out_of_range": self.input.out_of_range,
                    "webhook": self.webhook,
                    "panel": self.panel,
                    "camera": self.camera,
                    "errors": self.frame_errors,
                    "warnings": self.warnings,
                },
            )

    # -- operator input ---------------------------------------------------------------------------
    def _handle_event(self, event: pygame.event.Event, now: float) -> None:
        if event.type == pygame.QUIT:
            log.info("quit: the window was closed")
            self.running = False
        elif self.settings.open and event.type in SETTINGS_EVENTS:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_F12:
                self.save_screenshot()
            elif event.type != pygame.TEXTINPUT:  # nothing is typed into the name field meanwhile
                done = self.settings.handle(event)
                if done is not None:
                    self.close_settings(saved=done == "saved")
        elif event.type == pygame.TEXTINPUT:
            self.flow.register_type(event.text, now)
        elif event.type == pygame.KEYDOWN:
            key = event.key
            name = pygame.key.name(key)
            if self.capture is not None and key in (pygame.K_ESCAPE, pygame.K_c, pygame.K_BACKSPACE):
                if key == pygame.K_BACKSPACE:
                    self.capture.undo()
                else:
                    self.stop_capture("LiDAR calibration cancelled")
            elif key == pygame.K_ESCAPE:
                if not self.cfg.operator.double_press_to_quit or time.monotonic() - self._esc_at < 1.5:
                    log.info("quit: Esc")
                    self.running = False
                else:
                    self._esc_at = time.monotonic()
                    self._notify("Press Esc again to quit", 1.5)
            elif key == pygame.K_F2:
                self.calibration = not self.calibration
                if not self.calibration and self.capture is not None:
                    self.stop_capture("LiDAR calibration cancelled")
                self._update_cursor()
            elif key == pygame.K_c and self.calibration:
                self.start_capture()
            elif key == pygame.K_F6:
                self.rescan_lidar()
            elif key == pygame.K_F3:
                self.debug = not self.debug
                self._update_cursor()
            elif key == pygame.K_F5:
                self.open_settings()
            elif key == pygame.K_F12:
                self.save_screenshot()
            elif key == pygame.K_F11:
                self.toggle_fullscreen()
            elif self.flow.state == F.REGISTER:  # every other key belongs to the name field
                if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.flow.submit_registration(now)
                elif key == pygame.K_BACKSPACE:
                    self.flow.register_backspace(now)
                elif key == pygame.K_F4:
                    self.flow.reset(now)
            elif key == pygame.K_r:
                self.clock.resume()
                self.flow.reset(self.clock.now())
            elif key == pygame.K_p:
                if self.clock.paused:
                    self.clock.resume()
                elif self.flow.state in (F.COUNTDOWN, F.PLAYING):
                    self.clock.pause()
            elif key == pygame.K_m:
                self.flow.operator_missed_throw(now)
            elif name in self.cfg.operator.start_keys:
                if self.clock.paused:
                    self.clock.resume()
                else:
                    self.flow.request_start(now)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            norm = self.vp.to_norm(*event.pos)
            if norm is None:
                return
            if self.flow.state == F.REGISTER:  # the Start button also works with a real mouse
                self.flow.register_press(norm[0] * self.cfg.wall.width_cm, norm[1] * self.cfg.wall.height_cm, now)
            elif self.input.simulation:
                self.input.push_simulated(*norm)

    def _log_nearest_box(self, x: float, y: float, t: float) -> None:
        """For a miss: how far the closest box's hit box was (an offset of the lidar shows here)."""
        rnd = self.flow.round
        boxes = [tg for tg in rnd.targets if not tg.gone(t)] if rnd is not None else []
        if not boxes:
            return
        def outside(tg):
            cx, cy = tg.center_at(t)
            return max(abs(x - cx), abs(y - cy)) - tg.side_at(t) / 2 - rnd.tolerance
        tg = min(boxes, key=outside)
        cx, cy = tg.center_at(t)
        log.info("  nearest box: %s at (%.0f, %.0f), side %.0f cm; the hit was %.0f cm outside its hit box "
                 "(%.0f, %.0f from its centre)", f"{tg.tier} {tg.product}", cx, cy, tg.side_at(t), outside(tg),
                 x - cx, y - cy)

    # -- lidar (F2 then C, F6) ------------------------------------------------------------------
    def rescan_lidar(self) -> None:
        lidar = self.input.lidar
        if lidar is None:
            self._notify('No LiDAR: add "lidar" to input.sources', 4)
            return
        lidar.rescan()
        self._notify(f"LiDAR: scanning the background, keep the wall clear ({lidar.tuning.calibration_time} s)", 4)

    def start_capture(self) -> None:
        lidar = self.input.lidar
        if lidar is None:
            self._notify('No LiDAR: add "lidar" to input.sources', 4)
            return
        self.capture = self._new_capture()
        log.info("lidar calibration started: crosses at %s cm", [tuple(round(v) for v in t) for t in self.capture.targets])
        lidar.set_capture(True)  # the whole field counts until the wall is known again

    def _new_capture(self) -> Capture:
        tuning = self.input.lidar.tuning
        return Capture(self.cfg.wall.width_cm, self.cfg.wall.height_cm, time.perf_counter(), tuning.fitted or tuning.guide)

    def stop_capture(self, message: str = "", seconds: float = 4) -> None:
        self.capture = None
        if self.input.lidar is not None:
            self.input.lidar.set_capture(False)
        if message:
            log.info("lidar calibration: %s", message)
            self._notify(message, seconds)

    def _feed_capture(self) -> None:
        cap, lidar = self.capture, self.input.lidar
        refused, strays, noise = cap.refused, cap.strays, cap.noise
        took = lidar is not None and cap.feed(self.input.recent)
        if cap.refused > refused:
            log.info("lidar calibration: a hit on a cross already taken was left out")
            self._notify(f"That spot is a cross already taken: hit cross {cap.index + 1}", 4)
        if cap.noise > noise:
            spot = tuple(round(v) for v in cap.noise_at)
            log.info("lidar calibration: a spot that keeps firing by itself was left out (noise at %s mm)", spot)
            self._notify(f"LiDAR noise at {spot} mm left out (it fires by itself): hit cross {cap.index + 1}", 4)
        if cap.strays > strays:
            log.info("lidar calibration: a hit that does not fit the wall was left out")
            if cap.misses >= MISSES_FOR_UNDO and cap.index > 0:
                self._notify(f"Hits keep missing cross {cap.index + 1}: if cross {cap.index} was wrong, "
                             f"Backspace takes it back (C starts again)", 6)
            else:
                self._notify(f"That hit was not near cross {cap.index + 1}: keep the rest of the field clear", 4)
        if took:
            log.info("lidar calibration: cross %d of %d taken at %s mm", cap.index, len(cap.targets),
                     tuple(round(v) for v in cap.seen[-1]))
        if not took or not cap.done:
            return
        log.info("lidar calibration hits (mm): %s", cap.seen)
        try:
            wall_map = lidar.set_calibration(cap.pairs(), cap.fit())
        except ValueError as exc:
            log.warning("lidar calibration failed: %s", exc)
            self.capture = self._new_capture()
            self._notify(f"LiDAR calibration failed ({exc}): again from cross 1", 5)
            return
        points = cap.points()
        self.cfg.raw()["input"]["lidar"]["calibration_points"] = points
        log.info("lidar calibrated: error %.1f cm, worst %.1f cm, points %s", wall_map.error_cm, wall_map.worst_cm, points)
        saved = ""
        if self.settings.file is not None:
            try:
                write_changes(self.settings.file, self.settings.source, self.cfg.raw(),
                              [("input", "lidar", "calibration_points")])
                saved = ", saved"
            except (OSError, ValueError) as exc:
                log.warning("lidar calibration not saved: %s", exc)
                saved = ", NOT saved (see the log)"
        self.stop_capture(f"LiDAR calibrated: off by {wall_map.error_cm:.0f} cm on average, "
                          f"{wall_map.worst_cm:.0f} cm at most{saved}", 8)

    # -- settings page (F5) -------------------------------------------------------------------
    def open_settings(self) -> None:
        """The game stands still while the page is open: no time passes and no throw counts."""
        self.settings.show()
        self._settings_paused = not self.clock.paused
        self.clock.pause()
        self._update_cursor()

    def close_settings(self, saved: bool = False) -> None:
        self.settings.hide()
        if self._settings_paused:
            self.clock.resume()
            self._settings_paused = False
        unsaved = len(self.settings.changed())
        if saved:
            self._notify(f"Settings saved to {self.settings.file}", 6)
        elif unsaved:
            self._notify(f"{unsaved} setting(s) changed for now but not saved - F5 to save", 10)
        self.apply_display()
        self._update_cursor()

    def run_settings(self) -> int:
        """Only the settings page, without the game (main.py --settings). Save keeps the page open;
        Esc, or the button that leaves without saving, ends the program."""
        self.settings.standalone = True
        self.settings.show()
        self._update_cursor()
        pygame.display.set_caption("Market Pulse - settings")
        pg_clock = pygame.time.Clock()
        while self.settings.open:
            pg_clock.tick(30)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:  # the window's close button
                    self.settings.hide()
                elif event.type in SETTINGS_EVENTS and event.type != pygame.TEXTINPUT:
                    done = self.settings.handle(event)
                    if done == "saved":
                        self.settings.message = (f"ذخیره شد: {self.settings.file}", "ok")
                        self.apply_display()
                    elif done is not None:
                        self.settings.hide()
            if self.settings.open:
                self.settings.draw(self.screen, self.renderer.vp_menu, self.renderer.vp.rect)
                pygame.display.flip()
            if self.exit_after is not None and pygame.time.get_ticks() / 1000 >= self.exit_after:
                self.settings.hide()
        unsaved = self.settings.changed()
        if unsaved:
            log.info("settings page closed without saving %d change(s)", len(unsaved))
        self.shutdown()
        return 0

    def _settings_changed(self) -> None:
        """A value changed on the settings page: update the parts that keep a copy of it."""
        self.renderer.refresh_settings()
        self.audio.set_volume(self.cfg.audio.volume)
        if self.input.lidar is not None:
            self.input.lidar.tuning_changed()

    def _lidar_settings(self) -> Settings | None:
        """The lidar tool's settings for the LiDAR tab: the running lidar's own, or else read from
        input.lidar.settings_file (none there: no tab). Saved next to the game, not inside the .exe."""
        name = Path(self.cfg.input.lidar.settings_file)
        if self.input.lidar is not None:
            tuning = self.input.lidar.tuning
        else:
            found = InputManager._find(str(name), (self.paths.root, self.paths.bundle))
            if not found.is_file():
                return None
            tuning = Settings(str(found))
            tuning.load()
        tuning.path = str(name if name.is_absolute() else self.paths.root / name)
        return tuning

    def save_screenshot(self) -> Path | None:
        folder = self.paths.logs / "screenshots"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"market_pulse_{time.strftime('%Y%m%d_%H%M%S')}.png"
            pygame.image.save(self.screen, str(path))
            log.info("screenshot saved: %s", path)
            return path
        except (OSError, pygame.error) as exc:
            log.error("screenshot failed: %s", exc)
            return None

    def shutdown(self) -> None:
        self.input.stop()
        self.renderer.set_replay(None, None)
        if self.camera is not None:
            self.camera.stop()
        if self.panel is not None:
            self.panel.stop()
        if self.webhook is not None:
            self.webhook.stop()
        pygame.quit()


def render_gallery(cfg: Section, paths: Paths, out_dir: Path, seed: int = 7) -> list[Path]:
    """Render every screen to PNG with a scripted round (for review and tests).

    Uses a manual clock and seeded random numbers, so the same code and assets
    always give the same images (useful to compare a visual change).
    """
    fake = {"t": 1000.0}
    app = App(cfg, paths, clock_source=lambda: fake["t"], start_inputs=False)
    app.flow.store = None  # never write gallery rounds to the real results log
    app.flow.rng = np.random.default_rng(seed)
    app.effects.rng.seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    def step(seconds: float, fps: int = 60) -> None:
        for _ in range(max(1, int(seconds * fps))):
            fake["t"] += 1.0 / fps
            app.frame()

    def shot(name: str) -> None:
        path = out_dir / f"{len(saved) + 1:02d}_{name}.png"
        pygame.image.save(app.screen, str(path))
        saved.append(path)

    def throw_at(target_pick: Callable, offset=(0.0, 0.0)) -> None:
        rnd = app.flow.round
        tg = target_pick(rnd.targets)
        now = app.clock.now()
        x, y = tg.center_at(now)
        app.flow.handle_impact(x + offset[0], y + offset[1], now, now)

    def attract_at(scene: str, t: float, board: bool = False) -> None:  # the idle loop, that far into a scene
        fake["t"] = app.renderer.attract.start_of(scene, app.clock.now(), board) + t - 0.45
        app._last_now = app.clock.now()
        step(0.45)  # past the scenes' cross-fade

    attract_at("title", 1.5)
    shot("attract")
    if cfg.flow.attract_demo:
        A = app.renderer.attract
        for name, t in (("attract_demo_hit", A.HIT1 + 0.25), ("attract_demo_new", A.NEW + 0.6),
                        ("attract_demo_sizes", A.SIZES + 1.5), ("attract_demo_rush", A.HIT2 + 0.7)):
            attract_at("demo", t)
            shot(name)
    attract_at("title", 1.5)
    app.flow.request_start(app.clock.now())
    if app.flow.state == F.INSTRUCTIONS:
        step(3.0)
        shot("instructions")
        step(cfg.flow.instructions_s - 3.0 + 0.1)
    if app.flow.state == F.REGISTER:
        step(0.5)
        shot("register_empty")
        app.flow.submit_registration(app.clock.now())
        step(0.5)
        shot("register_error")
        app.flow.register_type("سارا", app.clock.now())
        step(0.5)
        shot("register_name")
        app.flow.submit_registration(app.clock.now())
    if app.flow.state == F.INTRO:  # the logo at rest, then zooming away
        step(1.2)
        shot("intro_logo")
        step(cfg.flow.intro_s - 1.2 - 0.6)
        shot("intro_zoom")
        step(0.6)
    step(0.5)
    shot("countdown")
    step(cfg.flow.countdown_s)
    step(0.25)
    shot("go")
    step(1.5)
    def by_tier(tier: str) -> Callable:  # a box of that size, or else the first box
        return lambda ts: next((t for t in ts if t.tier == tier and not t.gone(app.clock.now())), ts[0])

    tiny, medium, small = by_tier("tiny"), by_tier("medium"), by_tier("small")
    throw_at(medium)
    step(0.25)
    shot("hit_popup")
    step(0.55)
    throw_at(tiny)
    step(0.12)
    shot("hit_tiny_rush")
    if app.flow.round.rush is not None:
        step(0.5)
        shot("rush_fading_in")
    step(1.0)
    throw_at(small)
    step(0.15)
    shot("combo_x2")
    step(1.0)
    throw_at(tiny)
    step(1.0)
    throw_at(tiny)
    step(1.2)
    shot("field_rush")
    app.flow.handle_impact(150.0, 380.0, app.clock.now(), app.clock.now())  # a miss (footer area)
    step(0.15)
    shot("miss")
    rush = app.flow.round.rush
    if rush is not None:
        step(max(0.0, rush.ends_at - app.clock.now() - 0.4))
        shot("rush_fading_out")
        step(1.0)
        shot("after_rush")
    left = app.flow.round.time_left(app.clock.now())
    step(max(0.0, left - 6.4))
    shot("last_seconds")
    step(7.0)
    shot("ending")
    step(cfg.round.end_delay_s + 0.9)
    shot("results_reveal")  # the card's entrance, half way
    step(5.5)
    shot("results")
    app.flow.reset(app.clock.now())
    step(0.05)  # the ATTRACT state event reloads app.board from the (real) store; override it after that
    app.board = [
        {"player_name": "سارا", "score": 260, "timestamp": 1},
        {"player_name": "", "score": 215, "timestamp": 2},
        {"player_name": "Ali", "score": 150, "timestamp": 3},
    ]
    attract_at("board", 1.5, board=True)
    shot("attract_leaderboard")
    app.flow.request_start(app.clock.now())
    app.flow.request_start(app.clock.now())
    step(cfg.flow.countdown_s + 1.0)
    app.debug = True
    app.calibration = True
    app.input.recent.append((0.10, 0.12, 30.0, 48.0, True, "udp_json", time.perf_counter()))
    app.input.recent.append((0.52, 0.50, 156.0, 200.0, False, "tuio", time.perf_counter()))
    step(0.1)
    shot("debug_calibration")
    app.debug = app.calibration = False
    app.open_settings()
    step(0.1)
    shot("settings_boxes")
    app.settings.select_tab(1)
    step(0.1)
    shot("settings_multipliers")
    app.close_settings()
    app.shutdown()
    return saved
