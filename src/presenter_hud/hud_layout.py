from dataclasses import dataclass

import pygame


@dataclass(frozen=True)
class HudLayout:
    header: pygame.Rect
    slide: pygame.Rect
    coach: pygame.Rect
    position: pygame.Rect
    expression: pygame.Rect
    speech: pygame.Rect
    footer: pygame.Rect
    scale: float

    @property
    def regions(self) -> tuple[pygame.Rect, ...]:
        return self.header, self.slide, self.coach, self.position, self.expression, self.speech, self.footer


def make_layout(width: int, height: int) -> HudLayout:
    if width <= 0 or height <= 0:
        raise ValueError("Display dimensions must be positive")
    if height >= width:
        reference = (1080, 1920)
        regions = (
            (60, 32, 960, 86),
            (60, 144, 480, 270),
            (60, 1530, 462, 110),
            (60, 1660, 222, 180),
            (300, 1660, 222, 180),
            (558, 1530, 462, 310),
            (60, 426, 480, 32),
        )
    else:
        reference = (1280, 720)
        regions = (
            (32, 20, 1216, 64),
            (448, 112, 800, 450),
            (32, 112, 384, 152),
            (32, 300, 180, 280),
            (236, 300, 180, 280),
            (448, 590, 800, 62),
            (32, 674, 1216, 30),
        )
    sx, sy = width / reference[0], height / reference[1]
    rectangles = [
        pygame.Rect(round(x * sx), round(y * sy), max(1, round(w * sx)), max(1, round(h * sy)))
        for x, y, w, h in regions
    ]
    return HudLayout(*rectangles, min(sx, sy))
