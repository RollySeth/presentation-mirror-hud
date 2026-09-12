from __future__ import annotations

import argparse
from functools import lru_cache
import logging
import math
import time
from pathlib import Path

import pygame
from PIL import Image

from .hud_layout import make_layout
from .avatar import expression_avatar
from .models import HudState
from .state_store import StateStore


BACKGROUND = (0, 0, 0)
CYAN = (90, 238, 255)
NEON_BLUE = (0, 170, 255)
WHITE = (235, 248, 255)
MUTED = (164, 156, 188)
PURPLE = (171, 112, 255)
GREEN = (107, 214, 255)
AMBER = (255, 199, 112)


def advance_timer(state: HudState, elapsed: float) -> None:
    if state.running and not state.paused:
        state.elapsed_seconds = min(state.duration_seconds, state.elapsed_seconds + elapsed)
        if state.elapsed_seconds >= state.duration_seconds:
            state.running = False


def fit_image(image: pygame.Surface, bounds: pygame.Rect) -> pygame.Surface:
    scale = min(bounds.width / image.get_width(), bounds.height / image.get_height())
    size = (max(1, int(image.get_width() * scale)), max(1, int(image.get_height() * scale)))
    return pygame.transform.smoothscale(image, size)


def brighten_slide(image: pygame.Surface, boost: float) -> pygame.Surface:
    """Lift midtones without lifting black, clipping whites, or changing alpha."""
    if not 1 <= boost <= 2.5:
        raise ValueError("Slide brightness must be between 1 and 2.5")
    if boost == 1:
        return image
    pixels = Image.frombytes("RGBA", image.get_size(), pygame.image.tobytes(image, "RGBA"))
    curve = [round(255 * (level / 255) ** (1 / boost)) for level in range(256)]
    pixels = pixels.point(curve * 3 + list(range(256)))
    return pygame.image.frombytes(pixels.tobytes(), pixels.size, "RGBA").convert_alpha()


def wrap_text(font: pygame.font.Font, text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.size(candidate)[0] > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class HudRenderer:
    def __init__(self, size: tuple[int, int]):
        self.layout = make_layout(*size)
        self.surface = pygame.Surface(size).convert()
        scale = self.layout.scale
        self.small = pygame.font.SysFont("DejaVu Sans", max(8, round(17 * scale)))
        self.caption = pygame.font.SysFont("DejaVu Sans", max(10, round(21 * scale)))
        self.body = pygame.font.SysFont("DejaVu Sans", max(10, round(24 * scale)))
        self.title = pygame.font.SysFont("DejaVu Sans", max(11, round(26 * scale)), bold=True)
        self.coach_font = pygame.font.SysFont("DejaVu Sans", max(10, round(19 * scale)))
        self.large = pygame.font.SysFont("DejaVu Sans Mono", max(14, round(46 * scale)), bold=True)
        self.pad = max(4, round(20 * scale))
        self.slide_key = None
        self.slide_image = None
        self.slide_error = ""
        self.slide_retry_at = 0.0
        self.orb_position = None
        self.orb_updated_at = 0.0

    @lru_cache(maxsize=160)
    def text(self, font, content, color):
        return font.render(content, True, color)

    def text_block(self, text, font, color, bounds, max_lines=None):
        previous_clip = self.surface.get_clip()
        self.surface.set_clip(bounds)
        line_height = font.get_linesize()
        count = max(1, bounds.height // line_height)
        if max_lines is not None:
            count = min(count, max_lines)
        lines = wrap_text(font, text, bounds.width)
        for index, line in enumerate(lines[:count]):
            if index == count - 1 and len(lines) > count:
                line += "..."
            if font.size(line)[0] > bounds.width:
                while line and font.size(line + "...")[0] > bounds.width:
                    line = line[:-1]
                line += "..."
            self.surface.blit(self.text(font, line, color), (bounds.x, bounds.y + index * line_height))
        self.surface.set_clip(previous_clip)

    def card(self, bounds, label, accent=PURPLE):
        radius = max(3, round(16 * self.layout.scale))
        pad = min(max(4, round(12 * self.layout.scale)), max(4, bounds.height // 6))
        pygame.draw.rect(self.surface, (10, 8, 20), bounds, border_radius=radius)
        pygame.draw.rect(self.surface, (61, 37, 98), bounds, 1, border_radius=radius)
        label_bounds = pygame.Rect(
            bounds.x + pad, bounds.y + pad,
            max(1, bounds.width - 2 * pad), self.small.get_linesize(),
        )
        self.text_block(label, self.small, accent, label_bounds, 1)
        return pygame.Rect(
            bounds.x + pad, label_bounds.bottom + pad // 2,
            max(1, bounds.width - 2 * pad),
            max(1, bounds.bottom - label_bounds.bottom - pad * 2),
        )

    @lru_cache(maxsize=24)
    def dial(self, diameter: int, percent: int):
        image = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
        center = diameter / 2
        radius = diameter * 0.40
        width = max(2, round(diameter * 0.035))
        bounds = pygame.Rect(round(center - radius), round(center - radius), round(radius * 2), round(radius * 2))
        start, end = -math.pi / 4, math.pi * 5 / 4
        pygame.draw.arc(image, (66, 38, 111), bounds, start, end, width)
        for tick in range(33):
            angle = end - tick / 32 * math.pi * 1.5
            color = PURPLE if tick / 32 <= percent / 100 and percent > 0 else (39, 31, 57)
            inner, outer = radius * 1.13, radius * 1.2
            pygame.draw.line(
                image, color,
                (round(center + math.cos(angle) * inner), round(center - math.sin(angle) * inner)),
                (round(center + math.cos(angle) * outer), round(center - math.sin(angle) * outer)),
                max(1, width // 3),
            )
        if percent > 0:
            active_start = end - percent / 100 * math.pi * 1.5
            for extra, alpha in ((8, 15), (5, 25), (2, 45)):
                glow = bounds.inflate(extra, extra)
                pygame.draw.arc(image, (*PURPLE, alpha), glow, active_start, end, width + extra)
            pygame.draw.arc(image, PURPLE, bounds, active_start, end, width)
            pygame.draw.arc(image, (210, 197, 255), bounds.inflate(-width, -width), active_start, end, 1)
            marker = (
                round(center + math.cos(active_start) * radius),
                round(center - math.sin(active_start) * radius),
            )
            pygame.draw.circle(image, CYAN, marker, max(2, width // 2))
        return image

    def draw_slide(self, state, now):
        frame = self.layout.slide
        thickness = max(1, round(2 * self.layout.scale))
        pygame.draw.rect(self.surface, (0, 35, 65), frame, thickness * 3)
        pygame.draw.rect(self.surface, NEON_BLUE, frame.inflate(-2 * thickness, -2 * thickness), thickness)
        bounds = frame.inflate(-6 * thickness, -6 * thickness)
        if not state.slide_paths:
            self.slide_key = self.slide_image = None
            self.text_block(
                "Your slide preview appears here. Upload images from the controller.",
                self.body, MUTED, bounds.inflate(-2 * self.pad, -2 * self.pad), 2,
            )
            return
        path = state.slide_paths[min(state.slide_index, len(state.slide_paths) - 1)]
        if now < self.slide_retry_at:
            self.text_block(self.slide_error, self.body, AMBER, bounds, 2)
            return
        try:
            key = (path, Path(path).stat().st_mtime_ns, bounds.size, state.slide_opacity, state.slide_brightness)
            if key != self.slide_key:
                image = pygame.image.load(path).convert_alpha()
                self.slide_image = brighten_slide(fit_image(image, bounds), state.slide_brightness)
                self.slide_image.set_alpha(state.slide_opacity)
                self.slide_key = key
                self.slide_error = ""
        except (OSError, pygame.error) as error:
            logging.getLogger(__name__).warning("Could not load slide %s: %s", path, error)
            self.slide_image = None
            self.slide_key = None
            self.slide_error = "Slide unavailable. Check the image in the controller."
            self.slide_retry_at = now + 2
        if self.slide_image is not None:
            self.surface.blit(self.slide_image, self.slide_image.get_rect(center=bounds.center))
        else:
            self.text_block(self.slide_error, self.body, AMBER, bounds, 2)

    @lru_cache(maxsize=24)
    def orb(self, diameter: int, pulse: int):
        image = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
        center = (diameter // 2, diameter // 2)
        radius = max(2, diameter // 2 - 1)
        for fraction, alpha in ((1, 12), (.82, 24), (.65, 45), (.47, 85)):
            pygame.draw.circle(image, (*NEON_BLUE, alpha + pulse * 2), center, max(1, round(radius * fraction)))
        core = max(2, round(radius * (.27 + pulse * .004)))
        pygame.draw.circle(image, NEON_BLUE, center, core)
        pygame.draw.circle(image, (0, 220, 255), center, max(1, core // 2))
        return image

    @lru_cache(maxsize=16)
    def arrow_sprite(self, direction: int, width: int, height: int):
        samples = 4
        sprite = pygame.Surface((width * samples, height * samples), pygame.SRCALPHA)
        columns = max(3, min(11, round(width / 4)))
        columns += 1 - columns % 2
        rows = columns + 2
        dx, dy = width * .86 / (columns - 1), height * .86 / (rows - 1)
        radius = min(dx, dy) * .43
        dots = []
        for row in range(rows):
            edge = abs(row - rows // 2) / (rows // 2) * (columns - 1) / 2
            for column in range(columns):
                if not edge <= column <= edge + (columns - 1) / 2:
                    continue
                fade = column / (columns - 1)
                center = (round((width * .07 + column * dx) * samples),
                          round((height * .07 + row * dy) * samples))
                dot_radius = max(1, round(radius * (1 - .75 * fade) * samples))
                dots.append((center, dot_radius, round(255 - 100 * fade)))
        for center, dot_radius, alpha in dots:
            pygame.draw.circle(sprite, (*NEON_BLUE, alpha // 8), center, max(1, round(dot_radius * 1.7)))
        for center, dot_radius, alpha in dots:
            pygame.draw.circle(sprite, (*NEON_BLUE, alpha), center, dot_radius)
        image = pygame.transform.smoothscale(sprite, (width, height))
        return pygame.transform.flip(image, True, False) if direction > 0 else image

    def draw_slide_arrows(self, state: HudState):
        count = len(state.slide_paths)
        if count < 2:
            return
        index = max(0, min(state.slide_index, count - 1))
        frame = self.layout.slide
        scale = self.layout.scale
        gap = max(1, round(6 * scale))
        room = min(frame.left, self.surface.get_width() - frame.right)
        half_height = max(2, round(24 * scale))
        for region in self.layout.regions:
            if region.top >= frame.centery + half_height or region.bottom <= frame.centery - half_height:
                continue
            if region.right <= frame.left:
                room = min(room, frame.left - region.right)
            elif region.left >= frame.right:
                room = min(room, region.left - frame.right)
        width = max(2, min(round(44 * scale), room - 2 * gap))
        height = max(3, round(width * 48 / 44))
        margin = gap + (width + 1) // 2
        for direction, available in ((-1, index > 0), (1, index < count - 1)):
            if not available:
                continue
            x = frame.left - margin if direction < 0 else frame.right + margin
            sprite = self.arrow_sprite(direction, width, height)
            self.surface.blit(sprite, sprite.get_rect(center=(x, frame.centery)))

    def draw_position(self, state: HudState, now: float):
        metrics = state.metrics
        body = self.card(self.layout.position, "LIVE POSITION", NEON_BLUE)
        content_height = max(12, min(body.height - self.pad * 2, round(body.width * .52)))
        stage = pygame.Rect(
            body.x + self.pad, body.y + self.pad // 2,
            max(1, body.width - self.pad * 2), max(1, content_height - self.pad),
        )
        zone = pygame.Rect(stage.x + round(stage.width * .35), stage.y, max(1, round(stage.width * .3)), stage.height)
        pygame.draw.rect(self.surface, (5, 20, 33), zone)
        pygame.draw.rect(self.surface, (0, 55, 85), stage, 1)
        for fraction in (.25, .5, .75):
            x = stage.x + round(stage.width * fraction)
            pygame.draw.line(self.surface, (9, 37, 58), (x, stage.top), (x, stage.bottom - 1))
        pygame.draw.line(self.surface, (9, 37, 58), (stage.left, stage.centery), (stage.right - 1, stage.centery))
        if metrics.camera_active and metrics.face_present:
            diameter = max(2, min(
                round(64 * self.layout.scale),
                max(2, round(stage.height * .65)), max(2, round(stage.width * .65)),
            ))
            target = (
                stage.x + diameter / 2 + max(0, min(1, metrics.face_center_x)) * (stage.width - diameter),
                stage.y + diameter / 2 + max(0, min(1, metrics.face_center_y)) * (stage.height - diameter),
            )
            blend = 1 - math.exp(-max(0, now - self.orb_updated_at) / .18)
            self.orb_position = target if self.orb_position is None else tuple(
                previous + (current - previous) * blend
                for previous, current in zip(self.orb_position, target)
            )
            self.orb_updated_at = now
            image = self.orb(diameter, round((math.sin(now * 2.5) + 1) * 6))
            self.surface.blit(image, image.get_rect(center=tuple(round(v) for v in self.orb_position)))
            status = "CENTERED" if metrics.centered else "RE-CENTER"
        else:
            self.orb_position = None
            status = "STEP INTO VIEW" if metrics.camera_active else "CAMERA OFF"
        self.text_block(status, self.small, NEON_BLUE, pygame.Rect(body.x, body.y + content_height, body.width, self.small.get_linesize()), 1)
        note = "FACE POSITION" if body.width < 300 * self.layout.scale else "FACE POSITION / NOT EYE TRACKING"
        self.text_block(note, self.small, MUTED, pygame.Rect(body.x, body.bottom - self.small.get_linesize(), body.width, self.small.get_linesize()), 1)

    def draw_expression(self, state: HudState, now: float):
        avatar = expression_avatar(state)
        body = self.card(self.layout.expression, "EXPRESSION", NEON_BLUE)
        diameter = max(12, min(round(100 * self.layout.scale), body.width, body.height - 2 * self.small.get_linesize() - self.pad // 2))
        center = (body.centerx, body.y + diameter // 2)
        radius = max(5, diameter // 2 - 2)
        thickness = max(1, round(2 * self.layout.scale))
        pygame.draw.circle(self.surface, (18, 11, 34), center, radius)
        pygame.draw.circle(self.surface, PURPLE, center, radius, thickness)
        if avatar["eyes"] != "unknown":
            blink = now % 4.8 < .15
            for offset in (-1, 1):
                eye_x = center[0] + offset * radius // 3
                eye_y = center[1] - radius // 4
                eye = pygame.Rect(eye_x - thickness, eye_y, thickness * 2, max(3, radius // 4))
                if blink or avatar["eyes"] == "narrow":
                    pygame.draw.line(self.surface, NEON_BLUE, (eye_x - thickness * 2, eye_y + thickness), (eye_x + thickness * 2, eye_y + thickness), thickness)
                elif avatar["eyes"] == "smile":
                    pygame.draw.arc(self.surface, NEON_BLUE, eye.inflate(thickness * 3, 0), 0, math.pi, thickness)
                elif avatar["eyes"] == "wide":
                    pygame.draw.ellipse(self.surface, NEON_BLUE, eye.inflate(thickness * 2, 0), thickness)
                else:
                    pygame.draw.rect(self.surface, NEON_BLUE, eye, border_radius=thickness)
                brow = avatar["brows"]
                if brow == "raised":
                    pygame.draw.arc(self.surface, CYAN, pygame.Rect(eye_x - thickness * 3, eye_y - thickness * 5, thickness * 6, thickness * 3), 0, math.pi, thickness)
                elif brow != "none":
                    tilt = offset if brow == "worried" else -offset
                    if brow == "asymmetric" and offset == 1:
                        tilt = 0
                    pygame.draw.line(self.surface, CYAN,
                        (eye_x - thickness * 3, eye_y - thickness * 3 - tilt * thickness),
                        (eye_x + thickness * 3, eye_y - thickness * 3 + tilt * thickness), thickness)
            mouth = pygame.Rect(center[0] - radius // 2, center[1] + radius // 8, radius, max(4, radius // 2))
            if avatar["voice"] == "speaking":
                mouth.height = max(3, round(radius * .8 * avatar["mouth"] * (.75 + .25 * math.sin(now * 16))))
                mouth.centery = center[1] + radius // 3
                pygame.draw.ellipse(self.surface, CYAN, mouth)
            elif avatar["mouth_shape"] == "smile":
                pygame.draw.arc(self.surface, CYAN, mouth, math.pi, math.pi * 2, thickness)
            elif avatar["mouth_shape"] == "frown":
                pygame.draw.arc(self.surface, CYAN, mouth, 0, math.pi, thickness)
            elif avatar["mouth_shape"] in ("open", "round"):
                if avatar["mouth_shape"] == "round":
                    mouth.width = max(4, radius // 2)
                    mouth.centerx = center[0]
                else:
                    mouth.height = max(4, radius // 4)
                pygame.draw.ellipse(self.surface, CYAN, mouth, thickness)
            elif avatar["mouth_shape"] == "slant":
                pygame.draw.line(self.surface, CYAN, (mouth.left, mouth.centery - thickness), (mouth.right, mouth.centery + thickness), thickness)
            else:
                pygame.draw.line(self.surface, CYAN, (mouth.left, mouth.centery), (mouth.right, mouth.centery), thickness)
        else:
            marker = self.text(self.body, "?", MUTED)
            self.surface.blit(marker, marker.get_rect(center=center))
        label = pygame.Rect(body.x, body.bottom - 2 * self.small.get_linesize(), body.width, self.small.get_linesize())
        self.text_block(avatar["label"], self.small, NEON_BLUE, label, 1)
        self.text_block(avatar["activity"], self.small, MUTED, pygame.Rect(body.x, body.bottom - self.small.get_linesize(), body.width, self.small.get_linesize()), 1)

    def draw_speech(self, metrics, running=False):
        bounds = self.layout.speech
        rate = f"{metrics.words_per_minute:.0f}" if metrics.speech_active else "--"
        prefix = "~" if metrics.speech_pending_word_count else ""
        word_total = metrics.speech_word_count + metrics.speech_pending_word_count
        summary = f"{rate} WPM   {prefix}{word_total} words"
        if bounds.height < round(150 * self.layout.scale):
            pygame.draw.rect(self.surface, (10, 8, 20), bounds, border_radius=max(3, self.pad // 2))
            self.text_block(
                "SPEECH: " + summary, self.small, WHITE,
                bounds.inflate(-2 * self.pad, -max(2, round(bounds.height * 0.25))), 1,
            )
            return
        body = self.card(bounds, "SPEECH COACH", NEON_BLUE)
        diameter = max(12, min(body.height - self.pad * 2, (body.width - self.pad) // 2))
        gauge = pygame.Rect(body.x, body.y, diameter, diameter)
        percent = min(100, max(0, round(metrics.words_per_minute / 250 * 100))) if metrics.speech_active else 0
        self.surface.blit(self.dial(diameter, percent), gauge)
        value = self.text(self.large, rate, WHITE)
        self.surface.blit(value, value.get_rect(center=(gauge.centerx, gauge.centery - self.pad // 2)))
        label = self.text(self.small, "30s AVG / WPM", CYAN)
        self.surface.blit(label, label.get_rect(midtop=(gauge.centerx, gauge.centery + self.pad)))
        words = pygame.Rect(body.right - diameter, body.y, diameter, diameter)
        word_value = self.text(self.large, f"{prefix}{word_total}", WHITE)
        if word_value.get_width() > words.width:
            word_value = self.text(self.body, f"{prefix}{word_total}", WHITE)
        self.surface.blit(word_value, word_value.get_rect(center=(words.centerx, gauge.centery - self.pad // 2)))
        word_label = self.text(self.small, "WORDS", CYAN)
        self.surface.blit(word_label, word_label.get_rect(midtop=(words.centerx, gauge.centery + self.pad)))
        status = "LOCAL / LISTENING" if metrics.speech_active else "STARTING..." if running else "START A REHEARSAL"
        self.text_block(status, self.small, MUTED, pygame.Rect(body.x, gauge.bottom, body.width, self.small.get_linesize()), 1)
        bar = pygame.Rect(body.x, body.bottom - max(3, self.pad // 2), body.width, max(3, self.pad // 2))
        pygame.draw.rect(self.surface, (35, 24, 57), bar)
        if metrics.microphone_active:
            pygame.draw.rect(
                self.surface, GREEN,
                pygame.Rect(bar.x, bar.y, round(bar.width * max(0.0, min(1.0, metrics.volume))), bar.height),
            )

    def render(self, state: HudState, now: float) -> pygame.Surface:
        self.surface.fill(BACKGROUND)
        header = self.layout.header
        remaining = max(0, state.duration_seconds - state.elapsed_seconds)
        timer = self.text(self.large, f"{int(remaining // 60):02d}:{int(remaining % 60):02d}", NEON_BLUE)
        timer_rect = timer.get_rect(topright=header.topright)
        self.surface.blit(timer, timer_rect)
        title_bounds = pygame.Rect(header.x, header.y, max(1, header.width - timer_rect.width - self.pad), header.height)
        self.text_block(state.session_title, self.title, NEON_BLUE, title_bounds, 2)
        if state.paused:
            self.text_block("PAUSED", self.small, PURPLE, pygame.Rect(timer_rect.x, timer_rect.bottom, timer_rect.width, self.small.get_linesize()), 1)
        self.draw_slide(state, now)
        self.draw_slide_arrows(state)
        accent = AMBER if state.cue.level == "guide" else PURPLE if state.cue.level == "time" else GREEN
        body = self.card(self.layout.coach, "COACH", accent)
        self.text_block(state.cue.text, self.coach_font, WHITE, body, 3)
        self.draw_position(state, now)
        self.draw_expression(state, now)
        self.draw_speech(state.metrics, state.running)
        count = f"Slide {state.slide_index + 1}/{len(state.slide_paths)}" if state.slide_paths else "No slides loaded"
        microphone = f"{state.metrics.volume * 100:.0f}%" if state.metrics.microphone_active else "off"
        footer = f"{count}  |  Mic {microphone}"
        if self.surface.get_width() > self.surface.get_height():
            footer += "  |  Space: pause  Arrows: slides  Q: close"
        caption_font = self.caption if self.surface.get_height() >= self.surface.get_width() else self.small
        self.text_block(footer, caption_font, NEON_BLUE, self.layout.footer, 2)
        return pygame.transform.flip(self.surface, True, False) if state.mirror else self.surface


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--fps", type=int, default=20)
    args = parser.parse_args()
    if args.fps < 1:
        parser.error("--fps must be positive")

    pygame.init()
    width, height = (int(value) for value in args.size.lower().split("x"))
    flags = pygame.RESIZABLE if args.windowed else pygame.FULLSCREEN
    screen = pygame.display.set_mode((width, height), flags)
    pygame.display.set_caption("Presenter Mirror HUD")
    pygame.mouse.set_visible(args.windowed)
    clock = pygame.time.Clock()
    renderer = HudRenderer(screen.get_size())
    store = StateStore(args.state)
    last_tick = time.monotonic()
    pending_elapsed = 0.0
    running = True

    while running:
        now = time.monotonic()
        delta = now - last_tick
        last_tick = now
        state = store.load()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_SPACE:
                    store.update(lambda item: setattr(item, "paused", not item.paused))
                elif event.key in (pygame.K_RIGHT, pygame.K_n):
                    store.update(
                        lambda item: setattr(
                            item,
                            "slide_index",
                            min(item.slide_index + 1, max(0, len(item.slide_paths) - 1)),
                        )
                    )
                elif event.key in (pygame.K_LEFT, pygame.K_p):
                    store.update(lambda item: setattr(item, "slide_index", max(0, item.slide_index - 1)))
                elif event.key == pygame.K_m:
                    store.update(lambda item: setattr(item, "mirror", not item.mirror))

        if state.running and not state.paused:
            pending_elapsed += delta
            if pending_elapsed >= 0.5:
                state = store.update(lambda item: advance_timer(item, pending_elapsed))
                pending_elapsed = 0.0
        else:
            pending_elapsed = 0.0

        if renderer.surface.get_size() != screen.get_size():
            renderer.text.cache_clear()
            renderer.dial.cache_clear()
            renderer = HudRenderer(screen.get_size())
        screen.blit(renderer.render(state, now), (0, 0))
        pygame.display.flip()
        clock.tick(args.fps)

    pygame.quit()


if __name__ == "__main__":
    main()
