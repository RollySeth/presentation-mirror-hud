"""Install the optional OpenCV Zoo expression models; Python standard library only.

From the app directory: python deploy/setup_expression_models.py
For a completely offline install add --source DIRECTORY containing the four
manifest files. Downloads are installation-only; inference has no network code.
YuNet is MIT; MobileFaceNet Progressive Teacher is Apache-2.0. The corresponding
upstream licenses are verified and retained beside the weights. Existing Vosk
or other model directories are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import urlopen


def verified(path: Path, asset: dict) -> bool:
    if not path.is_file() or path.stat().st_size != asset["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == asset["sha256"]


def install_asset(asset: dict, directory: Path, source: Path | None = None) -> None:
    destination = directory / asset["file"]
    if verified(destination, asset):
        print(f"Verified existing {destination.name}")
        return
    part = destination.with_name(destination.name + ".part")
    created = False
    try:
        with part.open("xb") as output:
            created = True
            upstream = (source / asset["file"]).open("rb") if source is not None else urlopen(asset["url"], timeout=60)
            with upstream:
                remaining = asset["size"] + 1
                while remaining:
                    chunk = upstream.read(min(65536, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not verified(part, asset):
            raise ValueError(f"Size or SHA256 verification failed for {asset['file']}")
        os.replace(part, destination)
        print(f"Installed verified {destination.name}")
    finally:
        if created:
            part.unlink(missing_ok=True)


def install(manifest: Path, directory: Path, source: Path | None = None) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assets = data["licenses"] + data["models"]
    names = set()
    for asset in assets:
        name, url = asset["file"], urlparse(asset["url"])
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in names
                or not isinstance(asset["size"], int) or not 0 < asset["size"] <= 5_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
                or url.scheme != "https"
                or url.hostname not in {"raw.githubusercontent.com", "media.githubusercontent.com"}):
            raise ValueError("Invalid expression model manifest entry")
        names.add(name)
    directory.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        install_asset(asset, directory, source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("models/expressions"))
    parser.add_argument("--source", type=Path, help="Use existing local assets and licenses; no network access")
    args = parser.parse_args()
    try:
        install(Path(__file__).with_name("expression_models.json"), args.directory, args.source)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Expression model installation failed: {error}\n")


if __name__ == "__main__":
    main()
