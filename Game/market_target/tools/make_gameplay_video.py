"""A presentation video of one complete game, from the game itself (no screen recording).

    .venv\\Scripts\\python tools\\make_gameplay_video.py            -> out/gameplay_presentation.mp4
    .venv\\Scripts\\python tools\\make_gameplay_video.py --seconds 20   (a short test run)

The real game runs headless with config/settings.json and plays one whole session, frame by
frame on a simulated clock: started as the operator does (a name and Play on the phone
page), the countdown, a full round with thrown balls (most hit a box, some miss), the ending and the
results. Each frame is the game's own picture; a ball is drawn flying to where it lands. A
1920 x 1080 frame shows the wall on the right and, on the left, which step of the game this
is. No sound. H.264, 30 frames a second.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import av  # noqa: E402
import numpy as np  # noqa: E402
import pygame  # noqa: E402

from market_pulse import config  # noqa: E402
from market_pulse.app import App, Paths  # noqa: E402
from market_pulse.game import flow as F  # noqa: E402
from market_pulse.input.events import Impact  # noqa: E402
from market_pulse.ui import theme  # noqa: E402

FPS = 30
OUT_W, OUT_H = 1920, 1080
GAME_W, GAME_H = 810, 1080  # the 3:4 wall, the full height of the video
FLY_S = 0.4  # a ball's flight to the wall
MAX_S = 180.0  # the longest video
NAME = "آرمان"

STEPS = [  # (flow states, title, what happens)
    ({F.ATTRACT}, "شروع", "اپراتور نام بازیکن را می‌نویسد و بازی شروع می‌شود"),
    ({F.INSTRUCTIONS, F.REGISTER, F.INTRO, F.COUNTDOWN}, "آماده", "شمارش معکوس، بعد بازی شروع می‌شود"),
    ({F.PLAYING}, "بازی", "۶۰ ثانیه، توپ نامحدود: به باکس‌ها بزنید"),
    ({F.ENDING}, "پایان", "زمان تمام شد"),
    ({F.RESULTS}, "نتیجه", "امتیاز، امتیاز قرعه‌کشی و رتبه‌ی امروز"),
]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def game_settings(tmp: Path) -> Path:
    """config/settings.json with what the recording needs: no sensors or camera, its own results
    file, a 3:4 picture, the how-to page shown."""
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    raw.setdefault("display", {}).update({"resolution": [GAME_W, GAME_H], "fullscreen": False, "fill_screen": False,
                                          "scale": 1.0})
    raw.setdefault("input", {}).update({"sources": [], "simulation": False})
    raw.setdefault("camera", {})["enabled"] = False
    raw.setdefault("results", {}).update({"log_file": str(tmp / "results.jsonl"), "webhook_url": ""})
    raw.setdefault("web", {})["enabled"] = False
    raw.setdefault("flow", {}).update({"show_instructions": False})
    path = tmp / "settings.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


class Recorder:
    def __init__(self, out: Path, max_s: float | None):
        self.tmp = Path(tempfile.mkdtemp(prefix="video_"))
        cfg, warnings = config.load(game_settings(self.tmp))
        for w in warnings:
            print("config:", w)
        self.clock = Clock()
        self.app = App(cfg, Paths(ROOT), clock_source=self.clock, start_inputs=False)
        self.W, self.H = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
        self.frame_n = 0
        self.max_frames = int((max_s or MAX_S) * FPS)  # a run that went wrong never loops forever
        self.flights: list[tuple[float, float, float, float, float]] = []  # (land_t, x, y, from_x, from_y) cm
        self.rng = random.Random(7)
        self.canvas = pygame.Surface((OUT_W, OUT_H))
        out.parent.mkdir(parents=True, exist_ok=True)
        self.part = out.with_name(out.stem + ".part.mp4")  # the real file is replaced only when complete
        self.container = av.open(str(self.part), "w", options={"movflags": "+faststart"})
        self.stream = self.container.add_stream("libx264", rate=FPS)
        self.stream.width, self.stream.height = OUT_W, OUT_H
        self.stream.pix_fmt = "yuv420p"
        self.stream.options = {"crf": "19", "preset": "medium"}
        self.out = out

    # -- the simulated players ---------------------------------------------------------------------
    def wall_px(self, x: float, y: float) -> tuple[int, int]:
        return self.app.vp.px(x, y)

    def throw(self, x: float, y: float) -> None:
        """A ball leaves the player now and lands at (x, y) cm FLY_S later."""
        self.flights.append((self.clock.t + FLY_S, x, y, self.W / 2 + self.rng.uniform(-40, 40), self.H + 30))

    def _land(self) -> None:
        for f in [f for f in self.flights if f[0] <= self.clock.t]:
            self.flights.remove(f)
            _, x, y, _, _ = f
            self.app.input.queue.put(Impact(x / self.W, y / self.H, self.clock.t, "udp_json", (0.0, 0.0)))

    def throw_at_a_box(self) -> None:
        rnd = self.app.flow.round
        land = self.clock.t + FLY_S
        boxes = [t for t in rnd.targets if t.shown(self.clock.t) and t.shown(land) and t.die_at is None]
        if not boxes:
            return
        tg = self.rng.choice(boxes)
        x, y = tg.center_at(land)  # where it will be when the ball gets there
        self.throw(x + self.rng.uniform(-3, 3), y + self.rng.uniform(-3, 3))

    def throw_a_miss(self) -> None:
        rnd = self.app.flow.round
        land = self.clock.t + FLY_S
        for _ in range(40):
            x, y = self.rng.uniform(30, self.W - 30), self.rng.uniform(110, self.H - 60)
            if not any(t.contains(x, y, land, 12.0) for t in rnd.targets):
                self.throw(x, y)
                return

    # -- frames -------------------------------------------------------------------------------------
    def step(self, seconds: float) -> bool:
        """Run the game this long, writing every frame. False once the video is long enough."""
        for _ in range(max(1, int(round(seconds * FPS)))):
            self.clock.t += 1.0 / FPS
            self._land()
            self.app.frame()
            self._write()
            if self.max_frames is not None and self.frame_n >= self.max_frames:
                return False
        return True

    def _write(self) -> None:
        game = self.app.screen.copy()
        for land, x, y, fx, fy in self.flights:  # the balls in the air
            p = 1.0 - (land - self.clock.t) / FLY_S
            if 0.0 <= p <= 1.0:
                bx = fx + (x - fx) * p
                by = fy + (y - fy) * p - 70 * math.sin(p * math.pi)
                r = max(3, int(self.app.vp.sf(5.5 * (1 - 0.45 * p))))
                c = self.wall_px(bx, by)
                pygame.draw.circle(game, (0, 40, 60), (c[0] + 2, c[1] + 3), r)
                pygame.draw.circle(game, (255, 255, 255), c, r)
        cv = self.canvas
        cv.fill(theme.DEEP)
        gx = OUT_W - GAME_W - 70
        cv.blit(game, (gx, 0))
        pygame.draw.rect(cv, theme.lerp_color(theme.DEEP, theme.MIST, 0.35), (gx - 2, -2, GAME_W + 4, OUT_H + 4), 2)
        self._captions(cv, gx - 70)
        arr = pygame.surfarray.array3d(cv).swapaxes(0, 1)
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="rgb24")
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.frame_n += 1

    def _captions(self, cv: pygame.Surface, right: int) -> None:
        """Left of the wall: the game's name, the steps, the one now in turquoise, and what to do."""
        text = self.app.text
        T = self.app.renderer.T
        y = 90
        img = text.render(T.subtitle, "heading", 40, theme.TURQ)
        cv.blit(img, img.get_rect(topright=(right, y)))
        y += img.get_height() + 4
        img = text.render(T.title, "title", 96, theme.WHITE)
        cv.blit(img, img.get_rect(topright=(right, y)))
        y += img.get_height() + 50
        state = self.app.flow.state
        now_i = next((i for i, (states, _, _) in enumerate(STEPS) if state in states), 0)
        for i, (_, title, _) in enumerate(STEPS):
            on = i == now_i
            col = theme.TURQ if on else theme.lerp_color(theme.DEEP, theme.MIST, 0.55)
            label = text.render(title, "heading", 44 if on else 34, theme.WHITE if on else col)
            num = text.render(str(i + 1), "numbers", 44 if on else 34, col)
            cy = y + 34
            dot_r = 30 if on else 24
            pygame.draw.circle(cv, col, (right - dot_r, cy), dot_r, 0 if on else 3)
            if on:
                num = text.render(str(i + 1), "numbers", 40, theme.DEEP)
            cv.blit(num, num.get_rect(center=(right - dot_r, cy + 2)))
            cv.blit(label, label.get_rect(midright=(right - 2 * dot_r - 24, cy)))
            y += 78
        y += 30
        hint = STEPS[now_i][2]
        img = text.render(hint, "heading", 40, theme.WHITE)
        cv.blit(img, img.get_rect(topright=(right, y)))
        if state == F.PLAYING:  # the points by size, under it
            raw = self.app.cfg.raw()
            pts = "   ·   ".join(f"{T[f'tier_{t}']} +{raw['targets'][t]['points']}" for t in ("medium", "small", "tiny"))
            img = text.render(pts, "body", 32, theme.MIST)
            cv.blit(img, img.get_rect(topright=(right, y + 70)))

    def close(self, ok: bool = True) -> None:
        for packet in self.stream.encode():
            self.container.mux(packet)
        self.container.close()
        self.app.shutdown()
        if ok:
            os.replace(self.part, self.out)
        else:
            self.part.unlink(missing_ok=True)

    # -- the session --------------------------------------------------------------------------------
    def run(self) -> None:
        app, cfg = self.app, self.app.cfg
        # the attract loop at the start of its title scene: the name with the products around it
        if hasattr(app.renderer, "attract"):
            now = app.clock.now()
            self.clock.t += app.renderer.attract.start_of("title", now, False) - now + 0.05
        go = self.step(6.0)  # the attract screen
        if go and not app.flow.start_for(NAME, app.clock.now()):  # as the operator's phone page: a name and Play
            raise RuntimeError(f"the round did not start (state {app.flow.state})")
        while go and app.flow.state != F.PLAYING:
            go = self.step(0.2)
        n = 0
        while go and app.flow.state == F.PLAYING:  # the round: a ball every 1.5-2.4 s, about 1 in 6 a miss
            go = self.step(self.rng.uniform(1.5, 2.4))
            if go and app.flow.state == F.PLAYING and app.flow.round.time_left(app.clock.now()) > FLY_S + 0.2:
                n += 1
                self.throw_a_miss() if n % 6 == 0 else self.throw_at_a_box()
        while go and app.flow.state != F.RESULTS:
            go = self.step(0.2)
        if go:
            self.step(9.0)  # the results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "gameplay_presentation.mp4")
    ap.add_argument("--seconds", type=float, help="stop after this many seconds of video (a test)")
    args = ap.parse_args()
    rec = Recorder(args.out, args.seconds)
    ok = False
    try:
        rec.run()
        ok = True
    finally:
        rec.close(ok)
    print(f"{args.out}  {rec.frame_n / FPS:.1f} s, {rec.frame_n} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
