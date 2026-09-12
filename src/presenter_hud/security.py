"""Local controller credentials and explicit browser-origin policy."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def default_token_file() -> Path:
    return Path.home() / ".config" / "presentation-mirror-hud" / "controller.token"


def validate_token(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("Controller token must contain 32–256 URL-safe ASCII characters.")
    return token


def load_or_create_token(path: str | Path) -> str:
    path = Path(path).expanduser().absolute()
    if path.resolve().is_relative_to(REPOSITORY_ROOT):
        raise ValueError("Controller token file must be outside the repository.")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Controller token path must not contain symbolic links.")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise ValueError("Controller token must be a regular private file.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Controller token must be a regular private file.")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "r", encoding="ascii") as source:
            current = os.fstat(source.fileno())
            if not stat.S_ISREG(current.st_mode) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("Controller token file changed while opening.")
            if current.st_size > 258:
                raise ValueError("Controller token file is too large.")
            if os.name == "posix" and (current.st_mode & 0o077 or current.st_uid != os.getuid()):
                raise ValueError("Controller token must be owned by this user with permissions 0600.")
            try:
                token = source.read(258).strip()
            except UnicodeError:
                raise ValueError("Controller token file has invalid encoding.") from None
        return validate_token(token)
    else:
        token = secrets.token_urlsafe(48)
        with os.fdopen(fd, "w", encoding="ascii") as destination:
            destination.write(token + "\n")
            destination.flush()
            os.fsync(destination.fileno())
        return token


def normalize_origin(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in ("http", "https") or not host
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
            host = f"[{address}]" if address.version == 6 else str(address)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError
        suffix = f":{port}" if port is not None and port != (443 if parsed.scheme == "https" else 80) else ""
        return f"{parsed.scheme}://{host}{suffix}"
    except (TypeError, ValueError):
        raise ValueError("Allowed origins must be exact http(s) origins without paths or credentials.") from None


def loopback_origins(port: int) -> list[str]:
    return [normalize_origin(f"http://{host}:{port}") for host in ("127.0.0.1", "localhost", "[::1]")]


def origin_hosts(origins: list[str]) -> list[str]:
    return list(dict.fromkeys(urlsplit(origin).netloc.rsplit(":", 1)[0]
                             if urlsplit(origin).port is not None else urlsplit(origin).netloc
                             for origin in origins))
