"""
Settings side panel for the pygame window.

    click a tab            show that group of settings
    drag a slider          change a value
    mouse wheel on a row   fine tune, one step per tick
    click a number         type a value, Enter = ok, Esc = cancel
"""

import time

import pygame

from settings import TABS


PAD = 16
ROW_HEIGHT = {"number": 56, "bool": 40, "choice": 66}

BACK = (22, 24, 30)
ROW_HOVER = (30, 33, 42)
BORDER = (50, 54, 66)
TEXT = (225, 225, 230)
DIM = (135, 140, 155)
ACCENT = (70, 160, 255)
TRACK = (55, 60, 75)
BUTTON = (40, 44, 55)
BUTTON_HOVER = (58, 64, 80)
ON = (60, 190, 110)
SAVED = (90, 210, 130)

DEFAULT_HINT = ("Drag a slider, use the mouse wheel on a row to fine tune, "
                "or click a number to type it. Changes are saved automatically.")



def wrap(font, text, width):

    lines = []
    line = ""

    for word in text.split():

        test = f"{line} {word}".strip()

        if font.size(test)[0] <= width or not line:
            line = test
        else:
            lines.append(line)
            line = word

    if line:
        lines.append(line)

    return lines



class SettingsPanel:

    def __init__(self, cfg, on_change, buttons, width=340):
        """
        on_change(final): a value changed. final=False while a slider is held,
                          True when the change is done (save it now).
        buttons:          [(label, callback)] shown at the bottom.
        """

        self.cfg = cfg
        self.on_change = on_change
        self.buttons = list(buttons) + [("Reset tab", self.reset_tab)]
        self.width = width
        self.extra = {}             # tab name -> info lines under the rows

        self.font = pygame.font.SysFont("Arial", 17)
        self.small = pygame.font.SysFont("Arial", 14)
        self.title_font = pygame.font.SysFont("Arial", 21, bold=True)

        self.tab = 0
        self.rect = pygame.Rect(0, 0, width, 0)
        self.tab_rects = []
        self.rows = []              # (field, row rect, parts) of the shown tab
        self.button_rects = []

        self.drag = None            # field whose slider is held
        self.edit = None            # field whose number is being typed
        self.text = ""
        self.commit_at = None       # wheel ticks are saved a moment after the last one

        self.status = ""
        self.status_until = 0.0


    # -------------------------
    # LAYOUT
    # -------------------------

    def layout(self, rect):

        self.rect = rect
        x = rect.x + PAD
        w = rect.width - 2 * PAD

        n = len(TABS)
        tab_w = (w + 4) / n
        self.tab_rects = [
            pygame.Rect(round(x + i * tab_w), rect.y + 48, round(tab_w) - 4, 30)
            for i in range(n)
        ]

        y = rect.y + 94
        self.rows = []

        for field in TABS[self.tab][1]:

            if field.shape is not None and field.shape != self.cfg.shape:
                continue

            row = pygame.Rect(x, y, w, ROW_HEIGHT[field.kind])
            parts = {}

            if field.kind == "number":
                parts["value"] = pygame.Rect(row.right - 112, row.y + 4, 112, 24)
                parts["track"] = pygame.Rect(row.x + 9, row.y + 40, row.width - 18, 6)

            elif field.kind == "bool":
                parts["switch"] = pygame.Rect(row.right - 48, row.y + 8, 48, 24)

            else:
                k = len(field.options)
                bw = (w + 4) / k
                parts["options"] = [
                    pygame.Rect(round(x + i * bw), row.y + 28, round(bw) - 4, 30)
                    for i in range(k)
                ]

            self.rows.append((field, row, parts))
            y += row.height

        self.rows_bottom = y

        n = len(self.buttons)
        bw = (w + 6) / n
        top = rect.bottom - PAD - 36
        self.button_rects = [
            pygame.Rect(round(x + i * bw), top, round(bw) - 6, 36)
            for i in range(n)
        ]
        self.hint_top = top - 74


    def _row_at(self, pos):

        for field, row, parts in self.rows:
            if row.collidepoint(pos):
                return field, row, parts

        return None


    def _parts(self, field):

        for f, row, parts in self.rows:
            if f is field:
                return parts

        return None


    # -------------------------
    # EVENTS
    # -------------------------

    def handle(self, event):
        """Returns True when the panel used the event."""

        if self.edit is not None and event.type == pygame.KEYDOWN:

            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._commit_edit()
            elif event.key == pygame.K_ESCAPE:
                self.edit = None
            elif event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.unicode and event.unicode in "0123456789.-" and len(self.text) < 10:
                self.text += event.unicode

            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:

            if self.edit is not None:
                parts = self._parts(self.edit)
                if parts is None or not parts["value"].collidepoint(event.pos):
                    self._commit_edit()

            if not self.rect.collidepoint(event.pos):
                return False

            self._click(event.pos)
            return True

        if event.type == pygame.MOUSEMOTION and self.drag is not None:
            self._slide(event.pos[0])
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.drag is not None:
            self.drag = None
            self.on_change(True)
            return True

        if event.type == pygame.MOUSEWHEEL:

            pos = pygame.mouse.get_pos()

            if not self.rect.collidepoint(pos):
                return False

            hit = self._row_at(pos)

            if hit is not None and hit[0].kind == "number":
                field = hit[0]
                self.cfg.set(field.key, getattr(self.cfg, field.key) + event.y * field.step)
                self.on_change(False)
                self.commit_at = time.time() + 0.5

            return True

        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return self.rect.collidepoint(event.pos)

        return False


    def _click(self, pos):

        for i, rect in enumerate(self.tab_rects):
            if rect.collidepoint(pos):
                self.tab = i
                self.edit = None
                return

        for (label, callback), rect in zip(self.buttons, self.button_rects):
            if rect.collidepoint(pos):
                callback()
                return

        hit = self._row_at(pos)

        if hit is None:
            return

        field, row, parts = hit

        if field.kind == "number":

            if parts["value"].collidepoint(pos):
                self.edit = field
                self.text = ""

            elif pos[1] >= row.y + 28:
                self.drag = field
                self._slide(pos[0])

        elif field.kind == "bool":
            self.cfg.set(field.key, not getattr(self.cfg, field.key))
            self.on_change(True)

        else:
            for option, rect in zip(field.options, parts["options"]):
                if rect.collidepoint(pos):
                    self.cfg.set(field.key, option)
                    self.on_change(True)


    def _slide(self, mouse_x):

        field = self.drag
        parts = self._parts(field)

        if parts is None:
            return

        track = parts["track"]
        f = min(max((mouse_x - track.x) / track.width, 0.0), 1.0)

        self.cfg.set(field.key, field.low + f * (field.high - field.low))
        self.on_change(False)


    def _commit_edit(self):

        field = self.edit
        self.edit = None

        try:
            value = float(self.text)
        except ValueError:
            return

        self.cfg.set(field.key, value)
        self.on_change(True)


    def reset_tab(self):

        self.cfg.reset([field.key for field in TABS[self.tab][1]])
        self.edit = None
        self.on_change(True)


    def flash(self, text):

        self.status = text
        self.status_until = time.time() + 1.5


    def tick(self):
        """Call once per frame."""

        if self.commit_at is not None and time.time() >= self.commit_at:
            self.commit_at = None
            self.on_change(True)


    # -------------------------
    # DRAW
    # -------------------------

    def draw(self, screen):

        rect = self.rect
        mouse = pygame.mouse.get_pos()

        pygame.draw.rect(screen, BACK, rect)
        pygame.draw.line(screen, BORDER, rect.topleft, rect.bottomleft, 1)

        x = rect.x + PAD
        w = rect.width - 2 * PAD

        screen.blit(self.title_font.render("Settings", True, TEXT), (x, rect.y + 14))

        if time.time() < self.status_until:
            text = self.small.render(self.status, True, SAVED)
            screen.blit(text, (rect.right - PAD - text.get_width(), rect.y + 20))

        # tabs
        for i, (r, (name, _)) in enumerate(zip(self.tab_rects, TABS)):

            if i == self.tab:
                color = ACCENT
            elif r.collidepoint(mouse):
                color = BUTTON_HOVER
            else:
                color = BUTTON

            pygame.draw.rect(screen, color, r, border_radius=6)
            text = self.small.render(name, True, TEXT)
            screen.blit(text, text.get_rect(center=r.center))

        # rows
        hover = None

        for field, row, parts in self.rows:

            if row.collidepoint(mouse) or field is self.drag:
                hover = field
                pygame.draw.rect(screen, ROW_HOVER, row.inflate(10, 0), border_radius=6)

            value = getattr(self.cfg, field.key)
            screen.blit(self.font.render(field.label, True, TEXT), (row.x, row.y + 6))

            if field.kind == "number":
                self._draw_number(screen, field, value, parts, mouse)

            elif field.kind == "bool":
                sw = parts["switch"]
                pygame.draw.rect(screen, ON if value else TRACK, sw, border_radius=12)
                knob_x = sw.right - 12 if value else sw.x + 12
                pygame.draw.circle(screen, TEXT, (knob_x, sw.centery), 9)

            else:
                for option, r in zip(field.options, parts["options"]):
                    selected = option == value
                    color = ACCENT if selected else (BUTTON_HOVER if r.collidepoint(mouse) else BUTTON)
                    pygame.draw.rect(screen, color, r, border_radius=6)
                    text = self.small.render(option.capitalize(), True, TEXT)
                    screen.blit(text, text.get_rect(center=r.center))

        # info lines of this tab
        y = self.rows_bottom + 10

        for line in self.extra.get(TABS[self.tab][0], []):
            for part in wrap(self.small, line, w):
                screen.blit(self.small.render(part, True, DIM), (x, y))
                y += 18

        # hint for the row under the mouse
        hint = hover.help if hover is not None else DEFAULT_HINT
        y = max(self.hint_top, y + 8)
        pygame.draw.line(screen, BORDER, (x, y - 6), (x + w, y - 6), 1)

        for line in wrap(self.small, hint, w)[:4]:
            screen.blit(self.small.render(line, True, DIM), (x, y))
            y += 17

        # buttons
        for (label, _), r in zip(self.buttons, self.button_rects):
            color = BUTTON_HOVER if r.collidepoint(mouse) else BUTTON
            pygame.draw.rect(screen, color, r, border_radius=6)
            pygame.draw.rect(screen, BORDER, r, 1, border_radius=6)
            text = self.small.render(label, True, TEXT)
            screen.blit(text, text.get_rect(center=r.center))


    def _draw_number(self, screen, field, value, parts, mouse):

        box = parts["value"]
        track = parts["track"]

        if field is self.edit:
            pygame.draw.rect(screen, (15, 17, 22), box, border_radius=4)
            pygame.draw.rect(screen, ACCENT, box, 1, border_radius=4)
            cursor = "|" if int(time.time() * 2) % 2 == 0 else " "
            text = self.font.render(self.text + cursor, True, TEXT)
            screen.blit(text, (box.right - 8 - text.get_width(), box.y + 2))

        else:
            if box.collidepoint(mouse):
                pygame.draw.rect(screen, BORDER, box, 1, border_radius=4)
            text = self.font.render(field.format(value), True, ACCENT)
            screen.blit(text, (box.right - 8 - text.get_width(), box.y + 2))

        f = (value - field.low) / (field.high - field.low)
        knob_x = round(track.x + f * track.width)

        pygame.draw.rect(screen, TRACK, track, border_radius=3)
        pygame.draw.rect(screen, ACCENT, (track.x, track.y, knob_x - track.x, track.height), border_radius=3)
        pygame.draw.circle(screen, ACCENT if field is self.drag else TEXT, (knob_x, track.centery), 8)
