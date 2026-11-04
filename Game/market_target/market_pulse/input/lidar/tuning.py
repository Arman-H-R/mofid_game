"""
Touch settings. Copied from LIdar/settings.py: the game reads the same
settings.json format (config/lidar.json), so the lidar tool can tune it:
    python use_lib.py --settings <game folder>/config/lidar.json

Every value here can be changed live in the settings panel of the window
(python use_lib.py). Changes are saved to settings.json next to this file,
so the next start - window or --headless - uses the same calibration.
Delete settings.json to go back to the defaults.

Map coordinates (mm): what you see in the window. Lidar points are turned
by `rotation` and mirrored by `flip_x` / `flip_y`; the touch area, the angle
range and the OSC output all use these map coordinates.
"""

import json
import logging
import math
import os

import numpy as np


log = logging.getLogger(__name__)

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

SHAPES = ("circle", "square", "rectangle")



class Field:
    """One setting: default value, limits and the text shown in the panel."""

    def __init__(self, key, label, default, low=None, high=None, step=None,
                 unit="", help="", shape=None, options=None):

        self.key = key
        self.label = label
        self.default = default
        self.low = low
        self.high = high
        self.step = step
        self.unit = unit
        self.help = help
        self.shape = shape          # only shown for this area shape
        self.options = options

        if isinstance(default, bool):
            self.kind = "bool"
        elif options:
            self.kind = "choice"
        else:
            self.kind = "number"

        text = f"{step:g}" if step else ""
        self.decimals = len(text.split(".")[1]) if "." in text else 0


    def clean(self, value):
        """Any value (json, typed text, slider) -> valid value of this field."""

        if self.kind == "bool":
            return bool(value)

        if self.kind == "choice":
            return value if value in self.options else self.default

        value = min(max(float(value), self.low), self.high)
        value = self.low + round((value - self.low) / self.step) * self.step
        value = min(max(value, self.low), self.high)

        if isinstance(self.default, int):
            return int(round(value))

        return round(value, self.decimals)


    def format(self, value):

        if self.kind != "number":
            return str(value)

        text = f"{value:.{self.decimals}f}"

        if self.unit == "°":
            return text + "°"

        return f"{text} {self.unit}".strip()



# panel tabs, in order
TABS = [

    ("Area", [
        Field("shape", "Shape", "circle", options=SHAPES,
              help="Shape of the touch area. Only touches inside it count. "
                   "Drag the area on the map to move it."),
        Field("area_x", "Center X", 0, -5000, 5000, 10, "mm",
              help="Move the area left / right (or drag it on the map)."),
        Field("area_y", "Center Y", 0, -5000, 5000, 10, "mm",
              help="Move the area down / up (or drag it on the map)."),
        Field("radius", "Radius", 3000, 100, 6000, 10, "mm", shape="circle",
              help="Touches farther than this from the center are ignored."),
        Field("size", "Size", 2000, 100, 10000, 10, "mm", shape="square",
              help="Side length of the square."),
        Field("width", "Width", 1600, 100, 10000, 10, "mm", shape="rectangle",
              help="Rectangle width (x direction). Set it to your screen width."),
        Field("height", "Height", 900, 100, 10000, 10, "mm", shape="rectangle",
              help="Rectangle height (y direction). Set it to your screen height."),
    ]),

    ("Angle", [
        Field("rotation", "Rotate view", 0.0, -180, 180, 0.5, "°",
              help="Turn all lidar points so the screen edges line up with the "
                   "x / y axes of the map. Use it when the lidar is mounted at an angle."),
        Field("angle_from", "Angle from", 0, 0, 360, 1, "°",
              help="Touches only count between 'from' and 'to' (counter-clockwise "
                   "on the map, 0° = right). 0 to 360 = all around."),
        Field("angle_to", "Angle to", 360, 0, 360, 1, "°",
              help="Touches only count between 'from' and 'to' (counter-clockwise "
                   "on the map, 0° = right). 0 to 360 = all around."),
        Field("min_distance", "Min distance", 100, 0, 1000, 10, "mm",
              help="Ignore everything closer than this to the lidar (lidar cover, "
                   "frame, cables)."),
    ]),

    ("Detect", [
        Field("bg_margin", "Background gap", 40, 10, 300, 5, "mm",
              help="A point must be this much in front of the background to be new. "
                   "Lower = more sensitive, more noise (below ~25 mm lidar noise "
                   "starts to make touches)."),
        Field("bg_margin_ratio", "Extra gap far away", 2.0, 0.0, 10.0, 0.5, "%",
              help="Adds this % of the distance to the gap (lidar noise grows "
                   "with distance)."),
        Field("min_width", "Smallest object", 15, 5, 100, 1, "mm",
              help="Thinner objects are noise. Raise it if dust or cables make touches."),
        Field("max_width", "Biggest object", 600, 50, 2000, 10, "mm",
              help="Bigger objects (body, chair) are ignored."),
        Field("cluster_distance", "Join distance", 100, 20, 300, 5, "mm",
              help="Points closer than this are one object. Too low splits one hand "
                   "into fingers."),
        Field("touch_spacing", "Same hand radius", 150, 0, 500, 10, "mm",
              help="A new object this close to a touch is part of the same hand, "
                   "not a new touch. Lower it if two fingers close together must be "
                   "two touches."),
        Field("calibration_time", "Background scan time", 10, 2, 60, 1, "s",
              help="How long the empty area is scanned (at start and with "
                   "'Scan background')."),
    ]),

    ("Speed", [
        Field("confirm_scans", "Touch down after", 2, 1, 5, 1, "scans",
              help="Touch mode: scans in a row before TOUCH DOWN (1 scan = 0.1 s). "
                   "Lower = faster, higher = less noise. Ball mode always uses 1."),
        Field("release_time", "Touch up after", 0.25, 0.15, 2.0, 0.05, "s",
              help="A touch may vanish this long and keep its ID. "
                   "Higher = steadier, but TOUCH UP comes later. Min 0.15 s: the "
                   "lidar sends a scan only every 0.1 s."),
        Field("match_distance", "Max jump", 300, 50, 1000, 10, "mm",
              help="How far a touch may move between two scans. Raise it for very "
                   "fast swipes, lower it if IDs jump between hands."),
        Field("smooth_min_cutoff", "Smooth when still", 1.0, 0.1, 10.0, 0.1, "Hz",
              help="Lower = steadier position when the hand is still, but more lag."),
        Field("smooth_beta", "Follow fast moves", 0.010, 0.0, 0.1, 0.001, "",
              help="Higher = less lag when the hand moves fast."),
    ]),

    ("Ball", [
        Field("mode", "Mode", "touch", options=("touch", "ball"),
              help="Touch: follow hands, smooth moves. Ball: one hit per throw at the "
                   "FIRST x, y seen (it never moves), then a delay. For balls set "
                   "Detect > Smallest object to ~30 mm (ignores 1-point noise)."),
        Field("hit_delay", "Delay after hit", 1.0, 0.0, 5.0, 0.1, "s",
              help="Ball mode: after a hit, new hits are ignored for this long "
                   "(ball bouncing back, rolling, falling through the area)."),
        Field("hit_radius", "Delay radius", 0, 0, 3000, 50, "mm",
              help="Ball mode: 0 = the delay blocks the whole area. Above 0 only hits "
                   "closer than this to the last hit are blocked (several players)."),
        Field("ball_size", "Ball size", 0, 0, 300, 5, "mm",
              help="Ball mode: ball diameter. The lidar sees the front of the ball; "
                   "the hit is moved back by half of this to the ball center. 0 = off."),
        Field("show_hit", "Show hit for", 0.5, 0.1, 5.0, 0.1, "s",
              help="Ball mode: how long a hit stays on the map (shown at once, no "
                   "animation). Only the window, OSC is not changed."),
    ]),

    ("Output", [
        Field("flip_x", "Mirror X", False,
              help="Swap left and right (x -> -x). Turn on one mirror if the map "
                   "looks mirrored compared to the room."),
        Field("flip_y", "Mirror Y", False,
              help="Swap down and up (y -> -y)."),
        Field("normalize", "Send 0..1", False,
              help="Send x, y from 0 to 1 inside the area box (0, 0 = bottom left) "
                   "instead of mm."),
        Field("show_points", "Show lidar points", True,
              help="Draw all lidar points (blue = inside the area)."),
        Field("show_background", "Show background", True,
              help="Draw the learned background (grey)."),
    ]),
]

FIELDS = {field.key: field for _, fields in TABS for field in fields}



class Settings:

    def __init__(self, path=PATH):

        self.path = path
        self.reset()


    def reset(self, keys=None):

        for key in keys or FIELDS:
            setattr(self, key, FIELDS[key].default)


    def set(self, key, value):

        setattr(self, key, FIELDS[key].clean(value))


    def load(self):
        """Read the settings file. Returns True if it was found."""

        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)

        except FileNotFoundError:
            return False

        except (OSError, ValueError) as e:
            log.warning("%s not read (%s), using the lidar defaults", self.path, e)
            return False

        for key, value in data.items():

            if key in FIELDS:
                try:
                    self.set(key, value)
                except (TypeError, ValueError):
                    pass

        return True


    def save(self):

        data = {key: getattr(self, key) for key in FIELDS}
        tmp = self.path + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.replace(tmp, self.path)


    # -------------------------
    # GEOMETRY (map mm)
    # -------------------------

    def to_world(self, angle, dist):
        """Lidar angle (deg) + distance (mm) -> map x, y (mm)."""

        a = np.radians(angle + self.rotation)
        x = np.cos(a) * dist
        y = np.sin(a) * dist

        if self.flip_x:
            x = -x

        if self.flip_y:
            y = -y

        return x, y


    def half_size(self):

        if self.shape == "circle":
            return self.radius, self.radius

        if self.shape == "square":
            return self.size / 2, self.size / 2

        return self.width / 2, self.height / 2


    def area_box(self):
        """(x_min, x_max, y_min, y_max) around the area shape."""

        hw, hh = self.half_size()

        return self.area_x - hw, self.area_x + hw, self.area_y - hh, self.area_y + hh


    def in_shape(self, x, y):

        dx = x - self.area_x
        dy = y - self.area_y
        hw, hh = self.half_size()

        if self.shape == "circle":
            return dx * dx + dy * dy <= hw * hw

        return (np.abs(dx) <= hw) & (np.abs(dy) <= hh)


    def all_angles(self):

        return (self.angle_to - self.angle_from) % 360 == 0


    def in_angle(self, x, y):

        if self.all_angles():
            return np.ones(np.shape(x), dtype=bool)

        a = np.degrees(np.arctan2(y, x))
        span = (self.angle_to - self.angle_from) % 360

        return (a - self.angle_from) % 360 <= span


    def in_area(self, x, y, dist):
        """Map x, y (mm) and lidar distance (mm) -> inside the touch area."""

        ok = self.in_shape(x, y) & (dist >= self.min_distance)

        if not self.all_angles():
            ok &= self.in_angle(x, y)

        return ok


    def reach(self):
        """Farthest distance from the lidar that can be inside the area."""

        x0, x1, y0, y1 = self.area_box()

        return max(math.hypot(x, y) for x in (x0, x1) for y in (y0, y1))


    def output(self, x, y):
        """Map mm -> values sent to TouchDesigner (mm, or 0..1 in the area box)."""

        if not self.normalize:
            return x, y

        x0, x1, y0, y1 = self.area_box()

        return (x - x0) / (x1 - x0), (y - y0) / (y1 - y0)
