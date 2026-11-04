"""Sound feedback. Neither the scenario nor the brand book specifies sounds, so
short, soft tones are synthesised at start-up. To use designed sounds instead,
put WAV/OGG files named after the keys below in assets/sounds/ (for example
assets/sounds/hit_red.wav); any file found there replaces the synthesised one.
If the audio device cannot be opened, the game simply runs silently."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pygame

log = logging.getLogger(__name__)

C5, D5, E5, G5, A4, C6, E6, G4 = 523.25, 587.33, 659.25, 783.99, 440.0, 1046.5, 1318.5, 392.0

# name -> list of (frequency start, frequency end, start offset s, duration s, gain)
RECIPES: dict[str, list[tuple[float, float, float, float, float]]] = {
    "hit_green": [(C5, C5, 0.0, 0.16, 0.5), (G5, G5, 0.07, 0.22, 0.5)],
    "hit_blue": [(A4, A4, 0.0, 0.16, 0.5), (E5, E5, 0.07, 0.22, 0.5)],
    "hit_red": [(E5, E5, 0.0, 0.12, 0.45), (A4 * 2, A4 * 2, 0.05, 0.14, 0.45), (E6, E6, 0.10, 0.28, 0.4)],
    "spawn": [(420.0, 980.0, 0.0, 0.2, 0.25)],
    "miss": [(170.0, 85.0, 0.0, 0.2, 0.55)],
    "combo": [(C5, C5, 0.0, 0.1, 0.4), (E5, E5, 0.06, 0.1, 0.4), (G5, G5, 0.12, 0.1, 0.4), (C6, C6, 0.18, 0.25, 0.4)],
    "count": [(660.0, 660.0, 0.0, 0.14, 0.45)],
    "go": [(C5, C5, 0.0, 0.45, 0.35), (E5, E5, 0.0, 0.45, 0.35), (G5, G5, 0.0, 0.45, 0.35)],
    "warn": [(1000.0, 1000.0, 0.0, 0.06, 0.3)],
    "end": [(G5, G5, 0.0, 0.2, 0.4), (E5, E5, 0.12, 0.2, 0.4), (C5, C5, 0.24, 0.45, 0.4)],
    "start": [(G4, G4, 0.0, 0.14, 0.45), (C5, C5, 0.08, 0.3, 0.45)],
    "rush_start": [(300.0, 900.0, 0.0, 0.45, 0.4), (C5, C5, 0.3, 0.12, 0.4), (G5, G5, 0.38, 0.12, 0.4), (C6, C6, 0.46, 0.4, 0.4)],
    "rush_end": [(900.0, 300.0, 0.0, 0.5, 0.35), (C5, C5, 0.32, 0.35, 0.3)],
    "results": [(C5, C5, 0.0, 0.18, 0.35), (E5, E5, 0.1, 0.18, 0.35), (G5, G5, 0.2, 0.18, 0.35), (C6, C6, 0.3, 0.5, 0.35)],
}


def synth(recipe, rate: int) -> np.ndarray:
    end = max(off + dur for _, _, off, dur, _ in recipe)
    out = np.zeros(int(rate * (end + 0.05)), dtype=np.float64)
    for f0, f1, off, dur, gain in recipe:
        n = int(rate * dur)
        t = np.arange(n) / rate
        freq = np.linspace(f0, f1, n)
        phase = 2 * np.pi * np.cumsum(freq) / rate
        wave = np.sin(phase) + 0.18 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase)
        env = np.minimum(1.0, t / 0.006) * np.exp(-t * (4.5 / dur))
        start = int(rate * off)
        out[start : start + n] += wave * env * gain
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.8


class Audio:
    def __init__(self, enabled: bool, volume: float, sounds_dir: Path):
        self.ok = False
        self.volume = volume
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        if not enabled:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            rate, _size, channels = pygame.mixer.get_init()
            pygame.mixer.set_num_channels(24)
        except (pygame.error, TypeError) as exc:
            log.warning("audio disabled: %s", exc)
            return
        for name, recipe in RECIPES.items():
            snd = None
            for ext in (".wav", ".ogg"):
                path = sounds_dir / f"{name}{ext}"
                if path.is_file():
                    try:
                        snd = pygame.mixer.Sound(str(path))
                    except pygame.error as exc:
                        log.warning("cannot load %s: %s", path, exc)
                    break
            if snd is None:
                mono = (synth(recipe, rate) * 32767).astype(np.int16)
                arr = np.ascontiguousarray(np.repeat(mono[:, None], channels, axis=1)) if channels > 1 else mono
                snd = pygame.sndarray.make_sound(arr)
            snd.set_volume(volume)
            self.sounds[name] = snd
        self.ok = True

    def set_volume(self, volume: float) -> None:
        self.volume = volume
        for snd in self.sounds.values():
            snd.set_volume(volume)

    def play(self, name: str) -> None:
        if self.ok:
            snd = self.sounds.get(name)
            if snd is not None:
                snd.play()

    def on_event(self, name: str, data: dict) -> None:
        if name == "hit":
            o = data["outcome"]
            self.play(f"hit_{o.color}")
            if len(o.spawned) > 1:
                self.play("spawn")
            if o.level_up:
                self.play("combo")
        elif name in ("miss", "register_error"):
            self.play("miss")
        elif name in ("rush_start", "rush_again"):
            self.play("rush_start")
        elif name == "rush_end" and data.get("reason") == "time":
            self.play("rush_end")
        elif name == "count":
            self.play("count")
        elif name in ("go", "end", "results"):
            self.play(name)
        elif name == "time_warning":
            self.play("warn")
        elif name == "start_hit":
            self.play("start")
