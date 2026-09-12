from itertools import combinations

import pygame
import pytest

from presenter_hud.hud import HudRenderer, NEON_BLUE, WHITE, CYAN, brighten_slide, wrap_text
from presenter_hud.avatar import EXPRESSION_LABELS
from presenter_hud.language import LANGUAGE_ADVICE
from presenter_hud.hud_layout import make_layout
from presenter_hud.models import HudState


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280), (1920, 1080), (1280, 720), (320, 240)])
def test_layout_regions_do_not_overlap_or_leave_screen(size):
    layout = make_layout(*size)
    screen = pygame.Rect((0, 0), size)
    for region in layout.regions:
        assert screen.contains(region)
    for first, second in combinations(layout.regions, 2):
        assert not first.colliderect(second)


def test_portrait_slide_and_coach_leave_reflection_area_clear():
    layout = make_layout(1080, 1920)
    assert layout.slide.bottom < 1920 * 0.4
    assert layout.coach.width < 1080 * 0.55
    assert layout.coach.height < 1920 * 0.09
    assert not any(region.collidepoint(540, 960) for region in layout.regions)
    assert sum(region.width * region.height for region in layout.regions) < 1080 * 1920 * 0.25
    reflection = pygame.Rect(60, 480, 960, 1014)
    assert not any(region.colliderect(reflection) for region in layout.regions)
    assert layout.header == pygame.Rect(60, 32, 960, 86)
    assert layout.slide.left == layout.coach.left == layout.header.left
    assert layout.position.size == layout.expression.size == (222, 180)
    assert layout.coach.size == (462, 110)
    assert layout.speech.height == 310
    assert layout.position.top == layout.expression.top
    assert layout.coach.top == layout.speech.top
    assert layout.position.left == 1080 - layout.speech.right == 60
    assert layout.expression.left - layout.position.right == 18
    assert layout.speech.left - layout.expression.right == 36
    assert layout.coach.left == layout.position.left
    assert layout.coach.right == layout.expression.right
    assert layout.position.top - layout.coach.bottom == 20
    assert layout.position.bottom == layout.speech.bottom
    assert layout.footer.left == layout.slide.left
    assert layout.slide.bottom < layout.footer.top < layout.footer.bottom < layout.coach.top
    assert layout.footer.width == layout.slide.width
    assert layout.slide.size == (480, 270)
    assert 1920 - layout.speech.bottom == 80


@pytest.fixture
def display(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    HudRenderer.text.cache_clear()
    HudRenderer.dial.cache_clear()
    HudRenderer.orb.cache_clear()
    HudRenderer.arrow_sprite.cache_clear()
    pygame.quit()


def test_renderer_reuses_scaled_slide(display, tmp_path, monkeypatch):
    path = tmp_path / "slide.png"
    slide = pygame.Surface((640, 360))
    slide.fill((50, 80, 120))
    pygame.image.save(slide, str(path))
    calls = []
    original_load = pygame.image.load

    def load(name):
        calls.append(name)
        return original_load(name)

    monkeypatch.setattr(pygame.image, "load", load)
    state = HudState(mirror=False, slide_paths=[str(path)])
    renderer = HudRenderer((1080, 1920))
    renderer.render(state, 1)
    renderer.render(state, 2)
    assert calls == [str(path)]


def test_face_position_changes_the_live_marker(display):
    state = HudState(mirror=False)
    state.metrics.camera_active = True
    state.metrics.face_present = True
    state.metrics.face_center_x = 0.2
    renderer = HudRenderer((1080, 1920))
    first = renderer.render(state, 1).subsurface(renderer.layout.position).copy()
    state.metrics.face_center_x = 0.8
    second = renderer.render(state, 2).subsurface(renderer.layout.position)
    assert pygame.image.tobytes(first, "RGB") != pygame.image.tobytes(second, "RGB")


def test_missing_slide_is_reported_without_crashing(display, tmp_path, caplog):
    renderer = HudRenderer((1080, 1920))
    renderer.render(HudState(slide_paths=[str(tmp_path / "missing.png")]), 1)
    assert renderer.slide_error
    assert "Could not load slide" in caplog.text


def test_landscape_speech_summary_is_visible(display):
    renderer = HudRenderer((1280, 720))
    state = HudState(mirror=False)
    state.metrics.speech_active = True
    state.metrics.speech_word_count = 42
    image = renderer.render(state, 1)
    region = renderer.layout.speech
    assert any(
        image.get_at((x, y))[:3] == (235, 248, 255)
        for x in range(region.left, region.right)
        for y in range(region.top, region.bottom)
    )


def test_header_footer_and_slide_frame_use_saturated_blue(display, tmp_path):
    path = tmp_path / "opaque.png"
    slide = pygame.Surface((640, 360))
    slide.fill("white")
    pygame.image.save(slide, str(path))
    renderer = HudRenderer((1080, 1920))
    image = renderer.render(HudState(mirror=False, slide_paths=[str(path)], slide_opacity=255), 1)
    for region in (renderer.layout.header, renderer.layout.footer):
        assert pygame.mask.from_threshold(image.subsurface(region), NEON_BLUE, (1, 1, 1, 255)).count() > 0
    frame = renderer.layout.slide
    assert image.get_at((frame.left + 2, frame.centery))[:3] == NEON_BLUE


def test_position_orb_tracks_both_axes_and_disappears_when_face_is_lost(display):
    state = HudState(mirror=False)
    state.metrics.camera_active = state.metrics.face_present = True
    state.metrics.face_center_x = state.metrics.face_center_y = .2
    renderer = HudRenderer((1080, 1920))
    renderer.render(state, 1)
    first = renderer.orb_position
    state.metrics.face_center_x = state.metrics.face_center_y = .8
    renderer.render(state, 2)
    assert renderer.orb_position[0] > first[0]
    assert renderer.orb_position[1] > first[1]
    state.metrics.face_present = False
    renderer.render(state, 3)
    assert renderer.orb_position is None


def test_orb_glow_animates_without_inventing_position_changes(display):
    state = HudState(mirror=False)
    state.metrics.camera_active = state.metrics.face_present = True
    renderer = HudRenderer((1080, 1920))
    first = renderer.render(state, 1).subsurface(renderer.layout.position).copy()
    location = renderer.orb_position
    second = renderer.render(state, 1.5).subsurface(renderer.layout.position)
    assert renderer.orb_position == location
    assert pygame.image.tobytes(first, "RGB") != pygame.image.tobytes(second, "RGB")


def test_slide_brightness_lifts_midtones_but_preserves_black_white_and_alpha(display):
    image = pygame.Surface((3, 1), pygame.SRCALPHA)
    image.set_at((0, 0), (0, 0, 0, 255))
    image.set_at((1, 0), (64, 64, 64, 77))
    image.set_at((2, 0), (255, 255, 255, 200))
    result = brighten_slide(image, 1.4)
    assert result.get_at((0, 0)) == image.get_at((0, 0))
    assert 64 < result.get_at((1, 0)).r < 255
    assert result.get_at((1, 0)).a == 77
    assert result.get_at((2, 0)) == image.get_at((2, 0))


def test_brightness_is_cached_and_recomputed_only_after_adjustment(display, tmp_path, monkeypatch):
    path = tmp_path / "slide.png"
    image = pygame.Surface((64, 36))
    image.fill((50, 50, 50))
    pygame.image.save(image, str(path))
    calls = []
    original = brighten_slide

    def brighten(image, boost):
        calls.append(boost)
        return original(image, boost)

    monkeypatch.setattr("presenter_hud.hud.brighten_slide", brighten)
    renderer = HudRenderer((1080, 1920))
    state = HudState(mirror=False, slide_paths=[str(path)])
    renderer.render(state, 1)
    renderer.render(state, 2)
    state.slide_brightness = 2
    renderer.render(state, 3)
    assert calls == [1.4, 2]
    assert renderer.caption.get_height() > renderer.small.get_height()


def test_expression_smile_changes_the_emoji_without_moving_the_panels(display):
    state = HudState(mirror=False)
    state.metrics.camera_active = state.metrics.expression_active = state.metrics.face_present = True
    renderer = HudRenderer((1080, 1920))
    state.metrics.smile_detected = False
    first = renderer.render(state, 1).subsurface(renderer.layout.expression).copy()
    state.metrics.smile_detected = True
    second = renderer.render(state, 1).subsurface(renderer.layout.expression)
    assert pygame.image.tobytes(first, "RGB") != pygame.image.tobytes(second, "RGB")


def test_speaking_mouth_animates_and_filler_counts_are_not_drawn(display, monkeypatch):
    monkeypatch.setattr("presenter_hud.avatar.time.time", lambda: 100)
    state = HudState(mirror=False, running=True)
    state.metrics.speech_active = state.metrics.microphone_active = True
    state.metrics.speech_observed_at = 100
    state.metrics.speech_last_word_age = .2
    state.metrics.speech_filler_count = 999
    renderer = HudRenderer((1080, 1920))
    recorded = []
    draw = renderer.text_block

    def record(text, *args, **kwargs):
        recorded.append(text)
        return draw(text, *args, **kwargs)

    monkeypatch.setattr(renderer, "text_block", record)
    first = renderer.render(state, 1).subsurface(renderer.layout.expression).copy()
    second = renderer.render(state, 1.1).subsurface(renderer.layout.expression)
    assert pygame.image.tobytes(first, "RGB") != pygame.image.tobytes(second, "RGB")
    assert "Speaking" in recorded
    assert not any("filler" in text.lower() or "999" in text for text in recorded)


def test_compact_coach_fits_fixed_language_suggestions(display):
    renderer = HudRenderer((1080, 1920))
    body = renderer.card(renderer.layout.coach, "COACH")
    for advice in LANGUAGE_ADVICE.values():
        assert len(wrap_text(renderer.coach_font, advice, body.width)) * renderer.coach_font.get_linesize() <= body.height


def test_word_and_pace_values_and_labels_share_vertical_alignment(display):
    renderer = HudRenderer((1080, 1920))
    state = HudState(mirror=False)
    state.metrics.speech_active = True
    state.metrics.words_per_minute = state.metrics.speech_word_count = 120
    image = renderer.render(state, 1)
    bounds = renderer.layout.speech
    halves = (pygame.Rect(bounds.x, bounds.y, bounds.width // 2, bounds.height),
              pygame.Rect(bounds.centerx, bounds.y, bounds.width // 2, bounds.height))
    number_bounds, label_bounds = [], []
    for half in halves:
        number = pygame.mask.from_threshold(image.subsurface(half), WHITE, (1, 1, 1, 255)).get_bounding_rects()
        union = number[0].unionall(number)
        number_bounds.append((union.top, union.bottom))
        lower = pygame.Rect(half.x, half.y + union.bottom, half.width, half.height - union.bottom)
        labels = pygame.mask.from_threshold(image.subsurface(lower), CYAN, (1, 1, 1, 255)).get_bounding_rects()
        combined = labels[0].unionall(labels)
        label_bounds.append((combined.top + union.bottom, combined.bottom + union.bottom))
    assert number_bounds[0] == number_bounds[1]
    assert label_bounds[0] == label_bounds[1]


def test_all_seven_expression_faces_have_distinct_geometry(display, monkeypatch):
    monkeypatch.setattr("presenter_hud.avatar.time.time", lambda: 100)
    state = HudState(mirror=False)
    state.metrics.camera_active = state.metrics.expression_active = state.metrics.face_present = True
    state.metrics.expression_model_active = True
    state.metrics.expression_confidence = .9
    state.metrics.expression_observed_at = 100
    renderer = HudRenderer((1080, 1920))
    faces = set()
    region = renderer.layout.expression
    crop = pygame.Rect(region.x + 40, region.y + 34, region.width - 80, 78)
    for label in EXPRESSION_LABELS:
        state.metrics.expression_label = label
        faces.add(pygame.image.tobytes(renderer.render(state, 1).subsurface(crop), "RGB"))
    assert len(faces) == 7


@pytest.mark.parametrize("count,index,previous,following", [
    (0, 0, False, False), (1, 0, False, False),
    (3, 0, False, True), (3, 1, True, True), (3, 2, True, False),
])
def test_neon_slide_arrows_match_available_neighbors(display, count, index, previous, following):
    renderer = HudRenderer((1080, 1920))
    state = HudState(slide_paths=["unused"] * count, slide_index=index)
    renderer.surface.fill((0, 0, 0))
    renderer.draw_slide_arrows(state)
    frame = renderer.layout.slide
    sides = (pygame.Rect(frame.left - 56, frame.centery - 32, 56, 64),
             pygame.Rect(frame.right, frame.centery - 32, 56, 64))
    for region, expected in zip(sides, (previous, following)):
        # Supersampling blends pixels; still require a bright blue foreground.
        visible = pygame.mask.from_threshold(renderer.surface.subsurface(region), NEON_BLUE, (10, 25, 35, 255)).count() > 0
        assert visible is expected


def test_dotted_arrows_are_cached_mirrored_and_have_no_backplate(display):
    renderer = HudRenderer((1080, 1920))
    left = renderer.arrow_sprite(-1, 44, 48)
    right = renderer.arrow_sprite(1, 44, 48)
    assert renderer.arrow_sprite(1, 44, 48) is right
    assert pygame.image.tobytes(right, "RGBA") == pygame.image.tobytes(
        pygame.transform.flip(left, True, False), "RGBA"
    )
    assert left.get_at((0, 0)).a == 0
    assert left.get_at((41, 24)).a == 0
    assert len(pygame.mask.from_surface(left, 100).connected_components()) >= 12
    leading = sum(left.get_at((x, y)).a for x in range(11) for y in range(48))
    trailing = sum(left.get_at((x, y)).a for x in range(33, 44) for y in range(48))
    assert leading > trailing


@pytest.mark.parametrize("size", [(1080, 1920), (1280, 720), (320, 240)])
def test_dotted_arrows_fit_beside_slide_without_covering_widgets(display, size):
    renderer = HudRenderer(size)
    state = HudState(slide_paths=["unused"] * 3, slide_index=1)
    renderer.surface.fill((0, 0, 0))
    renderer.draw_slide_arrows(state)
    borders = (pygame.Rect(0, 0, 1, size[1]), pygame.Rect(size[0] - 1, 0, 1, size[1]))
    for region in (*renderer.layout.regions, *borders):
        assert pygame.mask.from_threshold(
            renderer.surface.subsurface(region), (0, 0, 0), (1, 1, 1, 255)
        ).count() == region.width * region.height
