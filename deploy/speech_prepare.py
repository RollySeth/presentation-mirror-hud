"""Download the official small English Vosk model, without touching app config."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import urllib.request
import zipfile


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"


def prepare_pi3_profile(model: Path) -> Path:
    """Trade some search accuracy for Pi 3 CPU headroom; keep the original."""
    profile = model.with_name(f"{MODEL_NAME}-pi3")
    if profile.exists():
        configuration = profile / "conf" / "model.conf"
        if (
            (profile / "am" / "final.mdl").is_file()
            and (profile / "conf" / "mfcc.conf").is_file()
            and configuration.is_file()
            and all(
                setting in configuration.read_text(encoding="utf-8").splitlines()
                for setting in ("--min-active=100", "--max-active=500", "--beam=7.0")
            )
        ):
            print(f"Pi 3 model already available: {profile}")
            return profile
        raise SystemExit(f"Pi 3 profile already exists: {profile}; remove it explicitly before regenerating")
    shutil.copytree(model, profile)
    configuration = profile / "conf" / "model.conf"
    text = configuration.read_text(encoding="utf-8")
    for name, value in (("min-active", "100"), ("max-active", "500"), ("beam", "7.0")):
        text, count = re.subn(rf"(?m)^--{name}=.*$", f"--{name}={value}", text)
        if count != 1:
            raise SystemExit("Unexpected official model configuration; cannot safely tune the Pi 3 profile")
    configuration.write_text(text, encoding="utf-8")
    print(f"Pi 3 model: {profile} (beam 7, max-active 500; lower search accuracy, lower CPU)")
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("models"))
    parser.add_argument("--pi3-profile", action="store_true", help="Also create a lower-CPU decoder profile; original model stays unchanged")
    args = parser.parse_args()
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    model = root / MODEL_NAME
    if (model / "am" / "final.mdl").is_file() and (model / "conf" / "mfcc.conf").is_file():
        print(f"Model already available: {model}")
        if args.pi3_profile:
            prepare_pi3_profile(model)
        return
    archive = root / f"{MODEL_NAME}.zip"
    partial = root / f"{MODEL_NAME}.zip.part"
    staging = root / f".{MODEL_NAME}.extracting"
    if staging.exists() or partial.exists():
        raise SystemExit("A previous speech model download is incomplete; remove its .part/.extracting files before retrying")
    try:
        digest = hashlib.sha256()
        size = 0
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "SmartMirror-offline-speech"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as target:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError("Unexpectedly large model download")
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError("Official model archive checksum mismatch; refusing to extract")
        staging.mkdir()
        with zipfile.ZipFile(partial) as package:
            total = 0
            for member in package.infolist():
                relative = Path(member.filename)
                target = (staging / relative).resolve()
                total += member.file_size
                if (
                    relative.is_absolute()
                    or not target.is_relative_to(staging)
                    or not relative.parts
                    or relative.parts[0] != MODEL_NAME
                    or total > 200 * 1024 * 1024
                    or (member.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe or unexpected model archive")
            package.extractall(staging)
        extracted = staging / MODEL_NAME
        if not (extracted / "am" / "final.mdl").is_file() or not (extracted / "conf" / "mfcc.conf").is_file():
            raise ValueError("Model archive is incomplete")
        if model.exists():
            raise SystemExit(f"Incomplete model directory already exists: {model}; remove it explicitly before retrying")
        extracted.rename(model)
        partial.replace(archive)
        print(f"Model: {model}")
        print(f"Download bytes: {size}; SHA256: {digest.hexdigest()}")
        print(f"Unpacked bytes: {sum(p.stat().st_size for p in model.rglob('*') if p.is_file())}")
        if args.pi3_profile:
            prepare_pi3_profile(model)
    finally:
        partial.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    main()
