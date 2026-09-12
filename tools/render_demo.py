"""Generate documentation artwork from synthetic data, without sensors or user state."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from PIL import Image, ImageDraw, ImageFont
import pygame

from presenter_hud.hud import HudRenderer
from presenter_hud.models import CoachCue, HudState, LiveMetrics


def demo_slide() -> Image.Image:
    image = Image.new("RGB", (960, 540), (4, 9, 23))
    draw = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=60)
    body = ImageFont.load_default(size=27)
    small = ImageFont.load_default(size=20)
    draw.rounded_rectangle((36, 34, 924, 506), radius=24, outline=(0, 170, 255), width=3)
    draw.text((70, 68), "PRESENTATION MIRROR HUD / DEMO", font=small, fill=(0, 170, 255))
    draw.text((70, 144), "Tell a clear story.", font=title, fill=(235, 248, 255))
    draw.text((70, 234), "One point. Clear evidence. A purposeful pause.", font=body, fill=(155, 168, 192))
    for x, number, label in ((70, "01", "FOCUS"), (358, "02", "EXPLAIN"), (646, "03", "PAUSE")):
        draw.rounded_rectangle((x, 330, x + 238, 448), radius=18, fill=(14, 17, 39))
        draw.text((x + 20, 347), number, font=body, fill=(171, 112, 255))
        draw.text((x + 20, 403), label, font=small, fill=(90, 238, 255))
    return image


def demo_state(image_path: Path) -> HudState:
    now = time.time()
    return HudState(
        running=True,
        mirror=False,
        session_title="Presentation Rehearsal",
        elapsed_seconds=164,
        slide_index=1,
        slide_paths=[str(image_path)] * 3,
        cue=CoachCue("Pause briefly after your main point.", kind="pace"),
        metrics=LiveMetrics(
            camera_active=True, microphone_active=True, speech_active=True,
            speech_word_count=248, speech_recent_word_count=18, speech_recent_wpm=136,
            speech_last_word_age=.2, speech_observed_at=now,
            words_per_minute=136, volume=.42, face_present=True, centered=True,
            face_center_x=.48, face_center_y=.5, face_motion=.005,
            expression_active=True, expression_model_active=True,
            expression_label="happy", expression_confidence=.9, expression_observed_at=now,
        ),
    )


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirror-demo-") as directory:
        sample = Path(directory) / "synthetic-demo.png"
        image = demo_slide()
        image.save(sample)
        image.save(output / "sample-slide.png")
        pygame.init()
        try:
            pygame.display.set_mode((1, 1))
            renderer = HudRenderer((1080, 1920))
            pygame.image.save(renderer.render(demo_state(sample), 1), output / "hud-demo.png")
        finally:
            pygame.quit()
    print("Generated synthetic HUD artwork; no user data or sensors were read.")


if __name__ == "__main__":
    main()
