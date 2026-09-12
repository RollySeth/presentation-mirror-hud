from __future__ import annotations

import shutil
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def discover_slides(directory: str | Path) -> list[str]:
    path = Path(directory)
    if not path.exists():
        return []
    return [
        str(item.resolve())
        for item in sorted(path.iterdir(), key=lambda value: value.name.lower())
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    ]


def install_slide_images(source_paths: list[str], destination: str | Path) -> list[str]:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for index, source_path in enumerate(source_paths, start=1):
        source = Path(source_path)
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported slide image: {source.name}")
        output = target / f"slide-{index:03d}{source.suffix.lower()}"
        shutil.copy2(source, output)
        installed.append(str(output.resolve()))
    return installed

